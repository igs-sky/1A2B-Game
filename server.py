# server.py
# -*- coding: utf-8 -*-

from __future__ import print_function, unicode_literals
import threading
import socket
import time
import six
from datetime import datetime
from uuid import uuid4

from package.game import ToolCard, Game
from package.player import Player
from package.redis_store import RedisStore

# Python2/3 兼容 Queue
try:
    import queue
except ImportError:
    import Queue as queue

try:
    import SocketServer  # Python 2
except ImportError:
    import socketserver as SocketServer  # Python 3


def format_log(msg):
    """回傳帶時間戳的 log 字串。"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return u"[{}] {}".format(timestamp, msg)


class ConnectionManager(object):
    """
    負責所有網路 I/O：
      - 接受新連線
      - 啟動「讀取指令」執行緒與「心跳檢測」執行緒
      - 斷線時通知遊戲主持
    """
    def __init__(self, host, port):
        # 建立 listener socket
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(5)

        self._redis_handler = RedisStore()

        # 等待配對的玩家佇列
        self._waiting_queue = queue.Queue()
        self._reconnect_queue = queue.Queue()

        # Active game sessions
        self.active_sessions = {}
        self._lock = threading.Lock()

    def serve_forever(self):
        """
        不斷 accept 新連線，為每位玩家建立 Player，
        並啟動兩條執行緒：_cmd_reader、_heartbeat。
        """
        print(format_log("伺服器已啟動，開始接受連線…"))
        while True:
            client_sock, client_addr = self.listener.accept()
            client_sock.sendall(b"CHECK_ID\n")
            player_id = client_sock.recv(1024).strip()

            # 建立 Player
            # TODO: 讓玩家的連線帶有 id 的參數 (如果有的話)
            player = Player(player_id)
            player.socket = client_sock
            player.address = client_addr
            player.cmd_queue = queue.Queue()
            player.heartbeat_queue = queue.Queue()
            player.is_alive = True

            # 啟動讀命令執行緒
            t1 = threading.Thread(target=self._cmd_reader, args=(player,))
            t1.daemon = True
            t1.start()
            # 啟動心跳檢測執行緒
            t2 = threading.Thread(target=self._heartbeat, args=(player,))
            t2.daemon = True
            t2.start()

            game_session_id = self._redis_handler.read_player_game(player_id)
            if game_session_id is None:
                # 推入等待佇列，交給配對器
                self._waiting_queue.put(player)
                print(format_log("%s 已連線，放入等待佇列" % player.name))
            else:
                if game_session_id in self.active_sessions:
                    session = self.active_sessions[game_session_id]
                    for i in range(len(session.players)):
                        if session.players[i].name == player.name:
                            session.players[i] = player
                            break
                else:
                    self._redis_handler.delete_game_state(game_session_id)
                    self._waiting_queue.put(player)
                    print(format_log("%s 已連線，放入等待佇列" % player.name))

    def _cmd_reader(self, player):
        """
        永遠從 socket.recv() 讀資料：
          - 收到空 bytes → 推入 DISCONNECT
          - 收到 HEARTBEAT_ACK → heartbeat_queue
          - 否則推入 cmd_queue
        """
        sock = player.socket
        buf = b""
        while True:
            try:
                data = sock.recv(1024)
            except Exception:
                player.cmd_queue.put({'type': 'DISCONNECT'})
                return
            if not data:
                player.cmd_queue.put({'type': 'DISCONNECT'})
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode('utf-8').strip()
                if text == "HEARTBEAT_ACK":
                    player.heartbeat_queue.put(True)
                else:
                    player.cmd_queue.put({'type': 'COMMAND', 'data': text})

    @staticmethod
    def send_to(player, msg):
        if not isinstance(msg, six.text_type):
            msg = msg.decode('utf-8')
        try:
            player.socket.sendall(msg.encode('utf-8'))
        except Exception:
            try:
                player.socket.close()
            except Exception:
                player.is_alive = False

    def _heartbeat(self, player, interval=5, timeout=10):
        """
        每隔 interval 秒發 HEARTBEAT，並在 timeout 秒內等 ACK；
        否則推入 DISCONNECT。
        """
        while True:
            try:
                print(format_log("%s - HEARTBEAT" % player.name))
                ConnectionManager.send_to(player, "HEARTBEAT\n")
            except Exception:
                player.cmd_queue.put({'type': 'DISCONNECTED'})
                return
            try:
                # 等待 ACK
                player.heartbeat_queue.get(timeout=timeout)
            except Exception:
                player.cmd_queue.put({'type': 'DISCONNECTED'})
                return
            time.sleep(interval)


class GameSession(object):
    """一對玩家的遊戲執行個體（Threaded）"""
    def __init__(self, p1, p2):
        self.players = [p1, p2]
        self._store_handler = RedisStore()
        self._id = uuid4()

    def _handle_disconnect(self, player):
        print(format_log("%s - DISCONNECTED" % player.name))
        self.broadcast("DISCONNECTED %s\n" % player.name, skip=player)
        player.is_alive = False

        if len(self.players) < 2:
            ConnectionManager.send_to(player, "WINNER\n")

    def broadcast(self, msg, skip=None):
        for p in self.players:
            if p is skip:
                continue
            ConnectionManager.send_to(p, msg)

    def _end_turn(self, game_state):
        self._store_handler.save_game_state(self._id, game_state)

    def _close_game(self):
        self._store_handler.delete_game_state(self._id)
        for p in self.players:
            p.socket.close()

    def _get_cmd(self, player):
        try:
            msg = player.cmd_queue.get()
        except Exception:
            self._handle_disconnect(player)
            return None

        if msg["type"] == "DISCONNECTED":
            self._handle_disconnect(player)
            return None

        return msg

    def run(self):
        game = Game(self.players)
        self._store_handler.save_game_state(self._id, game.to_dict())
        for player in self.players:
            self._store_handler.save_player_game(player.name, str(self._id))

        # 發初始手牌
        for p in self.players[1:]:
            nums  = ",".join(p.number_hand)
            tools = ",".join(p.tool_hand)
            ConnectionManager.send_to(p, "HAND %s;%s\n" % (nums, tools))

        # 回合循環
        while game.round < game.MAX_ROUNDS:
            if len(self.players) < 2:
                break

            for idx in range(len(self.players)):
                current  = self.players[idx]
                opponent = self.players[(idx+1) % 2]

                # 發送最新手牌
                nums = ",".join(current.number_hand)
                tools = ",".join(current.tool_hand)
                print(format_log("%s - HAND" % current.name))
                ConnectionManager.send_to(current, "HAND %s;%s\n" % (nums, tools))

                # 廣播狀態給對手
                for p in self.players:
                    if p is not current:
                        print(format_log("%s - STATUS" % p.name))
                        ConnectionManager.send_to(p, "STATUS %s\n" % current.name)

                # 道具階段
                print(format_log("%s - TOOL" % current.name))
                ConnectionManager.send_to(current, "TOOL\n")

                msg = self._get_cmd(current)
                if msg is None:
                    continue

                extra_guess = False
                if msg["type"] == "COMMAND" and msg["data"].isdigit():
                    ci = int(msg["data"]) - 1
                    if 0 <= ci < len(current.tool_hand):
                        tool = current.tool_hand.pop(ci)
                        print(format_log(u"%s - 使用 %s" % (current.name, tool)))
                        game.discard_tool.append(tool)

                        print(format_log("%s - USED_TOOL" % current.name))
                        ConnectionManager.send_to(current, "USED_TOOL %s\n" % tool)
                        print(format_log("BROADCAST(skip %s) - OPP_TOOL" % current.name))
                        self.broadcast("OPP_TOOL %s %s\n" % (current.name, tool), skip=current)

                        if tool == "POS":
                            # POS 道具處理
                            print(format_log("BROADCAST(skip %s) - POS" % current.name))
                            self.broadcast("POS %s %s\n" % (current.name, tool), skip=current)

                            pos_msg = self._get_cmd(current)
                            if pos_msg is None:
                                continue

                            pos = int(pos_msg["data"])
                            if len(self.players) < 2:
                                ConnectionManager.send_to(current, "WINNER\n")
                                return

                            opponent = self.players[(idx + 1) % 2]
                            digit = ToolCard.pos(opponent.answer, pos)
                            print(format_log("%s - POS_RESULT" % current.name))
                            ConnectionManager.send_to(current, "POS_RESULT %d %s\n" % (pos, digit))

                        elif tool == "SHUFFLE":
                            ToolCard.shuffle(current.answer)
                            print(format_log("%s - SHUFFLE_RESULT" % current.name))
                            ConnectionManager.send_to(current, "SHUFFLE_RESULT %s\n" % "".join(current.answer))

                        elif tool == "EXCLUDE":
                            if len(self.players) < 2:
                                ConnectionManager.send_to(current, "WINNER\n")
                                return
                            opponent = self.players[(idx + 1) % 2]
                            exclude_result = ToolCard.exclude(opponent.answer)
                            print(format_log("%s - EXCLUDE_RESULT" % current.name))
                            ConnectionManager.send_to(current, "EXCLUDE_RESULT %s\n" % exclude_result)

                        elif tool == "DOUBLE":
                            extra_guess = True
                            print(format_log("%s - DOUBLE_ACTIVE" % current.name))
                            ConnectionManager.send_to(current, "DOUBLE_ACTIVE\n")

                        elif tool == "RESHUFFLE":
                            ToolCard.reshuffle(current.number_hand, game.number_deck)
                            print(format_log("%s - RESHUFFLE_DONE" % current.name))
                            ConnectionManager.send_to(current, "RESHUFFLE_DONE\n")

                # 猜測階段
                guesses = 2 if extra_guess else 1
                for _ in range(guesses):
                    nums = ",".join(current.number_hand)
                    tools = ",".join(current.tool_hand)
                    print(format_log("%s - HAND" % current.name))
                    ConnectionManager.send_to(current, "HAND %s;%s\n" % (nums, tools))
                    print(format_log("%s - GUESS" % current.name))
                    ConnectionManager.send_to(current, "GUESS %s\n" % nums)

                    guess_msg = self._get_cmd(current)
                    if guess_msg is None:
                        continue

                    guess = str(guess_msg["data"])
                    print(format_log(u"%s - 猜了 %s" % (current.name, guess)))
                    for d in guess:
                        current.number_hand.remove(d)
                        game.discard_number.append(d)
                    game.draw_up(current)
                    a, b = game.check_guess(opponent.answer, list(guess))
                    print(format_log("%s - RESULT" % current.name))
                    ConnectionManager.send_to(current, "RESULT %d %d\n" % (a, b))
                    print(format_log("%s - OPP_GUESS" % opponent.name))
                    ConnectionManager.send_to(opponent, "OPP_GUESS %s %s %d %d\n" % (current.name, guess, a, b))

                    if a == game.NUM_GUESS_DIGITS:
                        # 猜中，全部玩家廣播勝利
                        self.broadcast("WINNER %s\n" % current.name)
                        print(format_log("%s - WINNER" % "BROADCAST"))
                        self._close_game()
                        return

            self._end_turn(game.to_dict())
            game.round += 1

        # 所有回合跑完，沒人猜中 → 平局
        for p in self.players:
            ConnectionManager.send_to(p, "DRAW\n")
        self._close_game()


def match_maker(conn_mgr):
    """不斷配對兩人一組，並開 Thread 執行"""
    while True:
        p1 = conn_mgr._waiting_queue.get()
        p2 = conn_mgr._waiting_queue.get()
        session = GameSession(p1, p2)
        conn_mgr.active_sessions[str(session._id)] = session
        print(format_log("配對到 %s 和 %s，啟動新遊戲房間" % (p1.name, p2.name)))
        t = threading.Thread(target=session.run)
        t.daemon = True
        t.start()


if __name__ == "__main__":
    HOST, PORT = '0.0.0.0', 12345
    connection_manager = ConnectionManager(HOST, PORT)
    # 啟動配對器 thread
    mt = threading.Thread(target=match_maker, args=(connection_manager,))
    mt.daemon = True
    mt.start()
    # 啟動伺服器
    connection_manager.serve_forever()
