# server.py
# -*- coding: utf-8 -*-

from __future__ import print_function, unicode_literals
import threading
import socket
import time
import six
from datetime import datetime
from uuid import uuid4

from package.game import ToolCard
# Python2/3 兼容 Queue
try:
    import queue
except ImportError:
    import Queue as queue

from package.player import Player
from package.game import Game

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

        # 等待配對的玩家佇列
        self.waiting = queue.Queue()

    def serve_forever(self):
        """
        不斷 accept 新連線，為每位玩家建立 Player，
        並啟動兩條執行緒：_cmd_reader、_heartbeat。
        """
        print(format_log("伺服器已啟動，開始接受連線…"))
        while True:
            client_sock, client_addr = self.listener.accept()
            # 建立 Player
            player = Player(uuid4())
            player.socket = client_sock
            player.address = client_addr
            player.cmd_queue = queue.Queue()
            player.heartbeat_queue = queue.Queue()

            # 啟動讀命令執行緒
            t1 = threading.Thread(target=self._cmd_reader, args=(player,))
            t1.daemon = True
            t1.start()
            # 啟動心跳檢測執行緒
            t2 = threading.Thread(target=self._heartbeat, args=(player,))
            t2.daemon = True
            t2.start()

            # 推入等待佇列，交給配對器
            self.waiting.put(player)
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
                pass

    def _heartbeat(self, player, interval=5, timeout=10):
        """
        每隔 interval 秒發 HEARTBEAT，並在 timeout 秒內等 ACK；
        否則推入 DISCONNECT。
        """
        while True:
            try:
                ConnectionManager.send_to(player, "HEARTBEAT\n")
            except Exception:
                player.cmd_queue.put({'type': 'DISCONNECT'})
                return
            try:
                # 等待 ACK
                player.heartbeat_queue.get(timeout=timeout)
            except Exception:
                player.cmd_queue.put({'type': 'DISCONNECT'})
                return
            time.sleep(interval)


class GameSession(object):
    """一對玩家的遊戲執行個體（Threaded）"""
    def __init__(self, p1, p2):
        self.players = [p1, p2]

    def broadcast(self, msg, skip=None):
        for p in self.players:
            if p is skip:
                continue
            ConnectionManager.send_to(p, msg)

    def run(self):
        game = Game(self.players)
        # 發初始手牌
        for p in self.players:
            nums  = ",".join(p.number_hand)
            tools = ",".join(p.tool_hand)
            ConnectionManager.send_to(p, "HAND %s;%s\n" % (nums, tools))

        # 回合循環
        for _ in range(game.MAX_ROUNDS):
            if len(self.players) < 2:
                break
            for idx in [0, 1]:
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

                try:
                    msg = current.cmd_queue.get()
                except Exception:
                    # 超時或例外 → 斷線
                    # TODO: 斷線處理
                    return

                if msg["type"] == "DISCONNECTED":
                    # TODO: 斷線處理
                    return

                extra_guess = False
                if msg["type"] == "COMMAND" and msg["data"].isdigit():
                    ci = int(msg["data"]) - 1
                    if 0 <= ci < len(current.tool_hand):
                        tool = current.tool_hand.pop(ci)
                        print(format_log(u"%s - 使用 %s" % (current.name, tool)))
                        game.discard_tool.append(tool)

                        print(format_log("%s - USED_TOOL" % current.name))
                        ConnectionManager.send_to(current, "USED_TOOL %s\n" % tool)
                        print(format_log("%s - OPP_TOOL" % opponent.name))
                        ConnectionManager.send_to(opponent, "OPP_TOOL %s %s\n" % (current.name, tool))

                        if tool == "POS":
                            # POS 道具處理
                            print(format_log("%s - POS" % current.name))
                            ConnectionManager.send_to(opponent, "POS\n")
                            try:
                                pos_msg = current.cmd_queue.get()
                            except Exception:
                                print(format_log("%s - WINNER" % opponent.name))
                                ConnectionManager.send_to(opponent, "WINNER %s\n" % opponent.name)
                                if current in self.players:
                                    self.players.remove(current)
                                return

                            if pos_msg["type"] == "DISCONNECTED":
                                print(format_log("%s - WINNER" % opponent.name))
                                ConnectionManager.send_to(opponent, "WINNER %s\n" % opponent.name)
                                if current in self.players:
                                    self.players.remove(current)
                                return

                            pos_str = pos_msg["data"]
                            while not (pos_str.isdigit() and 1 <= int(pos_str) <= game.NUM_GUESS_DIGITS):
                                ConnectionManager.send_to(current, "POS\n")
                                try:
                                    pos_msg = current.cmd_queue.get()
                                except Exception:
                                    print(format_log("%s - WINNER" % opponent.name))
                                    ConnectionManager.send_to(opponent, "WINNER %s\n" % opponent.name)
                                    if current in self.players:
                                        self.players.remove(current)
                                    return

                                if pos_msg["type"] == "DISCONNECTED":
                                    # TODO: 斷線處理
                                    return

                                pos_str = pos_msg["data"]

                            pi = int(pos_str)
                            digit = ToolCard.pos(opponent.answer, pi)
                            print(format_log("%s - POS_RESULT" % current.name))
                            ConnectionManager.send_to(current, "POS_RESULT %d %s\n" % (pi, digit))

                        elif tool == "SHUFFLE":
                            ToolCard.shuffle(current.answer)
                            print(format_log("%s - SHUFFLE_RESULT" % current.name))
                            ConnectionManager.send_to(current, "SHUFFLE_RESULT %s\n" % "".join(current.answer))

                        elif tool == "EXCLUDE":
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

                    try:
                        guess_msg = current.cmd_queue.get()
                    except Exception:
                        print(format_log("%s - WINNER" % opponent.name))
                        ConnectionManager.send_to(opponent, "WINNER %s\n" % opponent.name)
                        if current in self.players:
                            self.players.remove(current)
                        return

                    if guess_msg["type"] == "DISCONNECTED":
                        # TODO: 斷線異常
                        return

                    guess = guess_msg["data"]
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
                        # TODO: close the room
                        return

            # 所有回合跑完，沒人猜中 → 平局
            for p in self.players:
                ConnectionManager.send_to(p, "DRAW\n")
            # TODO: close the room


def match_maker(conn_mgr):
    """不斷配對兩人一組，並開 Thread 執行"""
    while True:
        p1 = conn_mgr.waiting.get()
        p2 = conn_mgr.waiting.get()
        print(format_log("配對到 %s 和 %s，啟動新遊戲房間" % (p1.name, p2.name)))
        t = threading.Thread(target=GameSession(p1, p2).run)
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
