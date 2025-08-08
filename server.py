# server.py
# -*- coding: utf-8 -*-

from __future__ import print_function, unicode_literals

import json
import threading
import socket
import time
import six
from datetime import datetime
from uuid import uuid4

from package.game import ToolCard, Game
from package.player import Player
from package.redis_store import RedisStore
from package.utils import format_log

# Python2/3 兼容 Queue
try:
    import queue
except ImportError:
    import Queue as queue

try:
    import SocketServer  # Python 2
except ImportError:
    import socketserver as SocketServer  # Python 3





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
            client_socket, client_address = self.listener.accept()
            print(format_log("client_socket={}, client_address={}".format(client_socket, client_address)))
            client_socket.sendall("CHECK_ID\n".encode("utf-8"))
            player_id = client_socket.recv(1024).strip()
            print(format_log("player_id={}".format(player_id)))
            game_session_id = self._redis_handler.read_player_game(player_id)
            print(format_log("game_session_id={}".format(game_session_id)))
            if game_session_id is None:
                self._redis_handler.delete_game_state(game_session_id)
                player = self._init_player_connection(Player(player_id), client_socket, client_address)
                self._waiting_queue.put(player)
                print(format_log("%s 已連線，放入等待佇列" % player.name))

            else:
                print(format_log("%s 正在重新連回 %s" % (player_id, game_session_id)))
                if game_session_id in self.active_sessions:
                    session = self.active_sessions[game_session_id]
                    print(format_log("%s 已找到斷線房間 %s" % (player_id, game_session_id)))
                    for i in range(len(session.players)):
                        player = session.players[i]
                        if player.name == player_id:
                            player = self._init_player_connection(player, client_socket, client_address)
                            session.players[i] = player
                            print(format_log("%s 已重新連線" % player.name))
                            ConnectionManager._send_last_action(player)
                            break
                else:
                    # 從 redis 復原 game session
                    game_state = self._redis_handler.read_game_state(game_session_id)
                    # print(format_log("%s 正在從 Redis 復原資料:\n %s" % (player_id, game_state)))
                    session = GameSession(Game.from_dict(game_state), game_session_id)
                    for p in session.players:
                        if p.name == player_id:
                            self._init_player_connection(p, client_socket, client_address)
                            ConnectionManager._send_last_action(p)
                            break
                    self.match_maker(session)

    @staticmethod
    def _send_last_action(player):
        nums = ",".join(player.number_hand)
        tools = ",".join(player.tool_hand)
        print(format_log("%s - HAND" % player.name))
        ConnectionManager.send_to(player, "HAND %s;%s\n" % (nums, tools))


        if len(player.action_histories) > 0:
            last_action = player.action_histories[-1]["action"]
            print(format_log("%s - %s" % (player.name, last_action[:-1])))
            ConnectionManager.send_to(player, last_action)

    def _init_player_connection(self, player, client_socket, client_address):
        # 建立 Player
        player.socket = client_socket
        player.address = client_address
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

        return player

    def _cmd_reader(self, player):
        """
        永遠從 socket.recv() 讀資料：
          - 收到空 bytes → 推入 DISCONNECTED
          - 收到 HEARTBEAT_ACK → heartbeat_queue
          - 否則推入 cmd_queue
        """
        sock = player.socket
        buf = b""
        while True:
            try:
                data = sock.recv(1024)
            except Exception:
                player.cmd_queue.put({'type': 'DISCONNECTED'})
                return
            if not data:
                player.cmd_queue.put({'type': 'DISCONNECTED'})
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
        if isinstance(msg, (dict, list)):
            msg = json.dumps(msg)  # 轉成 JSON 字串
        elif isinstance(msg, six.binary_type):  # bytes → decode 成 unicode
            msg = msg.decode('utf-8')
        elif not isinstance(msg, six.text_type):  # 其他不可辨識型別
            msg = unicode(msg) if six.PY2 else str(msg)
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
        否則推入 DISCONNECTED。
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

    def match_maker(self, game_session=None):
        """不斷配對兩人一組，並開 Thread 執行"""
        if game_session is not None:
            self.active_sessions[str(game_session.id)] = game_session
            print(format_log("重新啟動遊戲房間: %s" % ",".join([p.name for p in game_session.players])))
            t = threading.Thread(target=game_session.run, )
            t.daemon = True
            t.start()
            return

        while True:
            p1 = self._waiting_queue.get()
            p2 = self._waiting_queue.get()
            game_session = GameSession(Game([p1, p2]))

            self.active_sessions[str(game_session.id)] = game_session
            print(format_log("配對 %s 和 %s 到新遊戲房間" % (p1.name, p2.name)))
            t = threading.Thread(target=game_session.run, )
            t.daemon = True
            t.start()


class GameSession(object):
    """一對玩家的遊戲執行個體（Threaded）"""
    def __init__(self, game, session_id=None):
        self.players = game.players
        self.game = game
        self._store_handler = RedisStore()
        self.id = uuid4() if session_id is None else session_id

    def _handle_disconnect(self, player):
        print(format_log("%s - DISCONNECTED" % player.name))
        self.broadcast("DISCONNECTED %s\n" % player.name, skip=player)
        player.is_alive = False

    def broadcast(self, msg, skip=None):
        for p in self.players:
            if p is skip:
                continue
            ConnectionManager.send_to(p, msg)

    def _end_turn(self, game_state):
        self._store_handler.save_game_state(self.id, game_state)

    def _close_game(self):
        self._store_handler.delete_game_state(self.id)
        # print("Close game: %s" % self.players)
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
        game = self.game
        self._store_handler.save_game_state(self.id, game.to_dict())
        for player in self.players:
            self._store_handler.save_player_game(player.name, str(self.id))

        # 發初始手牌
        for p in self.players[1:]:
            nums  = ",".join(p.number_hand)
            tools = ",".join(p.tool_hand)
            ConnectionManager.send_to(p, "HAND %s;%s\n" % (nums, tools))

        # 回合循環
        while game.round < game.MAX_ROUNDS:
            if len(self.players) < 2:
                print(format_log("房間人數低於2人"))
                break

            while game.current_player_idx < len(self.players):

                idx = game.current_player_idx
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
                current.add_action_history(action="TOOL\n")
                ConnectionManager.send_to(current, "TOOL\n")

                msg = self._get_cmd(current)
                if msg is None:
                    game.current_player_idx += 1
                    continue

                extra_guess = False
                if msg["type"] == "COMMAND" and msg["data"].isdigit():
                    ci = int(msg["data"]) - 1
                    tool = current.tool_hand.pop(ci)
                    print(format_log(u"%s - 使用 %s" % (current.name, tool)))
                    game.discard_tool.append(tool)

                    print(format_log("%s - USED_TOOL" % current.name))
                    ConnectionManager.send_to(current, "USED_TOOL %s\n" % tool)
                    print(format_log("BROADCAST(skip %s) - OPP_TOOL" % current.name))
                    self.broadcast("OPP_TOOL %s %s\n" % (current.name, tool), skip=current)

                    if tool == "POS":
                        # POS 道具處理
                        print(format_log("%s - POS" % current.name))
                        current.add_action_history(action="POS\n")
                        ConnectionManager.send_to(current, "POS %s %s\n" % (current.name, tool))

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
                    current.add_action_history(action=("GUESS %s\n" % nums))
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
                    # RESULT 必須存放，否則GUESS如果玩家有猜完，在重連後會
                    current.add_action_history(action="RESULT %d %d\n" % (a, b))
                    ConnectionManager.send_to(current, "RESULT %d %d\n" % (a, b))
                    print(format_log("%s - OPP_GUESS" % opponent.name))
                    ConnectionManager.send_to(opponent, "OPP_GUESS %s %s %d %d\n" % (current.name, guess, a, b))

                    if a == game.NUM_GUESS_DIGITS:
                        # 猜中，全部玩家廣播勝利
                        self.broadcast("WINNER %s\n" % current.name)
                        print(format_log("%s - WINNER" % "BROADCAST"))
                        self._close_game()
                        return

                game.current_player_idx += 1
                self._end_turn(game.to_dict())

            game.current_player_idx = 0
            game.round += 1

        # 所有回合跑完，沒人猜中 → 平局
        for p in self.players:
            ConnectionManager.send_to(p, "DRAW\n")
        self._close_game()

if __name__ == "__main__":
    HOST, PORT = '0.0.0.0', 12345
    connection_manager = ConnectionManager(HOST, PORT)

    # 啟動配對器 thread
    mt = threading.Thread(target=connection_manager.match_maker)
    mt.daemon = True
    mt.start()

    # 啟動伺服器
    connection_manager.serve_forever()
