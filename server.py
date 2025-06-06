# server.py
# -*- coding: utf-8 -*-
from __future__ import print_function
import threading
import socket
from datetime import datetime

from gevent import monkey; monkey.patch_all()
import gevent
from gevent.timeout import Timeout
from gevent.queue import Queue

from package.player import Player
from package.game import Game, ToolCard

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
      - 啟動「讀取指令」協程與「心跳檢測」協程
      - 斷線時通知遊戲主持
    """
    def __init__(self, host, port):
        # 建立 listener socket
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(5)

        self.players = []  # 存放 Player 物件
        self.start_event = threading.Event()  # 兩人連線後觸發
        self.end_event = threading.Event()    # 遊戲結束後觸發

        # 保護 players 清單的鎖，防止同時多個 handler 產生 race condition
        self._lock = threading.Lock()

    def serve_forever(self):
        """
        不斷 accept 新連線，為每位玩家建立 Player 物件，
        並啟動兩條綠線程：_cmd_reader、_heartbeat。
        """
        while not self.end_event.is_set():
            try:
                client_sock, client_addr = self.listener.accept()
            except Exception:
                break  # 可能 listener 被關了
            with self._lock:
                if len(self.players) >= 2:
                    client_sock.sendall("FULL")
                    continue

                player_id = len(self.players) + 1
            player_obj = Player(u"玩家{}".format(player_id))
            player_obj.socket = client_sock
            player_obj.address = client_addr
            # 兩個佇列：cmd_queue 存一般指令，heartbeat_queue 存心跳回覆
            player_obj.cmd_queue = Queue()
            player_obj.heartbeat_queue = Queue()

            with self._lock:
                self.players.append(player_obj)

            print(format_log(u"%s 已連線" % player_obj.name))

            # 啟動「命令讀取」與「心跳檢測」協程
            gevent.spawn(self._heartbeat, player_obj)
            gevent.spawn(self._cmd_reader, player_obj)

            # 如果剛好是第 2 個玩家，觸發 start_event
            with self._lock:
                if len(self.players) == 2:
                    self.start_event.set()

    def _cmd_reader(self, player):
        """
        永遠從 player.socket.recv() 讀資料：
          - 如果讀到 ""，表示對方優雅關閉 → 推入 {'type': 'DISCONNECT'}
          - 如果讀到 "HEARTBEAT_ACK"，推到 heartbeat_queue
          - 否則，推到 cmd_queue，交由遊戲主持處理
        """
        sock = player.socket
        buffer = ""
        while not self.end_event.is_set():
            try:
                data = sock.recv(1024)
            except Exception:
                # recv 出錯視為斷線
                player.cmd_queue.put({"type": "DISCONNECT"})
                break

            if not data:
                # 客戶端關閉 connection
                player.cmd_queue.put({"type": "DISCONNECT"})
                break

            buffer += data
            # 以 '\n' 分割每一行
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                text = line.decode("utf-8").strip()

                if text == "HEARTBEAT_ACK":
                    player.heartbeat_queue.put(True)
                else:
                    player.cmd_queue.put({"type": "COMMAND", "data": text})

    def _heartbeat(self, player, interval=2, timeout=5):
        """
        每隔 interval 秒發 HEARTBEAT\n，並在 timeout 秒內等待 HEARTBEAT_ACK。
        如果超時或 sendall 失敗，就推入 {'type': 'DISCONNECT'}，結束心跳協程。
        """

        sock = player.socket
        while not self.end_event.is_set():
            try:
                sock.sendall("HEARTBEAT\n")
            except Exception:
                # 無法傳送心跳 → 視為斷線
                player.cmd_queue.put({"type": "DISCONNECTED"})
                break

            try:
                with Timeout(timeout):
                    # 等待 HEARTBEAT_ACK 被放入 heartbeat_queue
                    player.heartbeat_queue.get()

            except Exception:
                # 心跳超時或例外 → 視為斷線
                player.cmd_queue.put({"type": "DISCONNECTED"})
                break

            gevent.sleep(interval)

        # 心跳協程結束，close socket
        try:
            player.socket.close()
        except Exception:
            pass

    def shutdown(self):
        """
        在伺服器整體要關閉時呼叫：設置 end_event，並關閉 listener socket。
        """
        self.end_event.set()
        try:
            self.listener.close()
        except Exception:
            pass


class GameHost(object):
    """
    負責遊戲回合流程，從每位 player.cmd_queue.get() 拿指令並執行邏輯。
    斷線時處理勝負並結束遊戲。
    """
    def __init__(self, connection_manager):
        self.connection_manager = connection_manager
        self.players = connection_manager.players

    def handle_disconnect(self, current):
        if current in self.players:
            self.players.remove(current)

        if len(self.players) == 1:
            lone = self.players[0]
            lone.socket.sendall(("WINNER %s\n" % lone.name).encode("utf-8"))
            self.connection_manager.end_event.set()

    def broadcast(self, msg, skip_players=None):
        dead = []
        for player in self.players:
            if skip_players and player in skip_players:
                continue
            try:
                player.socket.sendall(msg.encode('utf-8'))
            except Exception:
                dead.append(player)
        for p in dead:
            self.players.remove(p)

    def run_game(self):
        # 等待兩位玩家都連線後開始
        self.connection_manager.start_event.wait()

        # 建立 Game 物件
        game = Game(self.players)

        # 發初始手牌給所有玩家
        for p in self.players:
            hand_nums = ",".join(p.number_hand)
            hand_tools = ",".join(p.tool_hand)
            p.socket.sendall(("HAND %s;%s\n" % (hand_nums, hand_tools)).encode("utf-8"))

        current_round = 1
        MAX_ROUNDS = game.MAX_ROUNDS

        while current_round <= MAX_ROUNDS:
            # 如果只剩一位玩家，直接宣告勝利
            if len(self.players) == 1:
                lone = self.players[0]
                lone.socket.sendall(("WINNER %s\n" % lone.name).encode("utf-8"))
                self.connection_manager.end_event.set()
                return

            # 依序讓玩家輪流操作
            for idx in [0, 1]:
                # 如果索引超過玩家數量，跳過
                if idx >= len(self.players):
                    break

                current = self.players[idx]
                opponent = self.players[(idx + 1) % len(self.players)]

                # 發送最新手牌
                nums = ",".join(current.number_hand)
                tools = ",".join(current.tool_hand)
                current.socket.sendall(("HAND %s;%s\n" % (nums, tools)).encode("utf-8"))

                # 廣播狀態給對手
                for p in self.players:
                    if p is not current:
                        p.socket.sendall(("STATUS %s\n" % current.name).encode("utf-8"))

                # 道具階段
                current.socket.sendall("TOOL\n")
                try:
                    msg = current.cmd_queue.get(timeout=15)
                except Exception:
                    # 超時或例外 → 斷線
                    self.handle_disconnect(current)
                    return

                if msg["type"] == "DISCONNECTED":
                    self.handle_disconnect(current)
                    return

                extra_guess = False
                if msg["type"] == "COMMAND" and msg["data"].isdigit():
                    ci = int(msg["data"]) - 1
                    if 0 <= ci < len(current.tool_hand):
                        tool = current.tool_hand.pop(ci)
                        print(format_log(u"%s - 使用 %s" % (current.name, tool)))
                        game.discard_tool.append(tool)

                        current.socket.sendall(("USED_TOOL %s\n" % tool).encode("utf-8"))
                        opponent.socket.sendall(("OPP_TOOL %s %s\n" % (current.name, tool)).encode("utf-8"))

                        if tool == "POS":
                            # POS 道具處理
                            current.socket.sendall("POS\n")
                            try:
                                pos_msg = current.cmd_queue.get(timeout=15)
                            except Exception:
                                opponent.socket.sendall(("WINNER %s\n" % opponent.name).encode("utf-8"))
                                if current in self.players:
                                    self.players.remove(current)
                                self.connection_manager.end_event.set()
                                return

                            if pos_msg["type"] == "DISCONNECTED":
                                opponent.socket.sendall(("WINNER %s\n" % opponent.name).encode("utf-8"))
                                if current in self.players:
                                    self.players.remove(current)
                                self.connection_manager.end_event.set()
                                return

                            pos_str = pos_msg["data"]
                            while not (pos_str.isdigit() and 1 <= int(pos_str) <= game.NUM_GUESS_DIGITS):
                                current.socket.sendall("POS\n")
                                try:
                                    pos_msg = current.cmd_queue.get(timeout=15)
                                except Exception:
                                    opponent.socket.sendall(("WINNER %s\n" % opponent.name).encode("utf-8"))
                                    if current in self.players:
                                        self.players.remove(current)
                                    self.connection_manager.end_event.set()
                                    return

                                if pos_msg["type"] == "DISCONNECTED":
                                    self.handle_disconnect(current)
                                    return

                                pos_str = pos_msg["data"]

                            pi = int(pos_str)
                            digit = ToolCard.pos(opponent.answer, pi)
                            current.socket.sendall(("POS_RESULT %d %s\n" % (pi, digit)).encode("utf-8"))

                        elif tool == "SHUFFLE":
                            ToolCard.shuffle(current.answer)
                            current.socket.sendall(("SHUFFLE_RESULT %s\n" % "".join(current.answer)).encode("utf-8"))

                        elif tool == "EXCLUDE":
                            exclude_result = ToolCard.exclude(opponent.answer)
                            current.socket.sendall(("EXCLUDE_RESULT %s\n" % exclude_result).encode("utf-8"))

                        elif tool == "DOUBLE":
                            extra_guess = True
                            current.socket.sendall("DOUBLE_ACTIVE\n")

                        elif tool == "RESHUFFLE":
                            ToolCard.reshuffle(current.number_hand, game.number_deck)
                            current.socket.sendall("RESHUFFLE_DONE\n")

                # 猜測階段
                guesses = 2 if extra_guess else 1
                for _ in range(guesses):
                    nums = ",".join(current.number_hand)
                    tools = ",".join(current.tool_hand)
                    current.socket.sendall(("HAND %s;%s\n" % (nums, tools)).encode("utf-8"))
                    current.socket.sendall(("GUESS %s\n" % nums).encode("utf-8"))

                    try:
                        guess_msg = current.cmd_queue.get(timeout=15)
                    except Exception:
                        opponent.socket.sendall(("WINNER %s\n" % opponent.name).encode("utf-8"))
                        if current in self.players:
                            self.players.remove(current)
                        self.connection_manager.end_event.set()
                        return

                    if guess_msg["type"] == "DISCONNECTED":
                        self.handle_disconnect(current)
                        return

                    guess = guess_msg["data"]
                    print(format_log(u"%s - 猜了 %s" % (current.name, guess)))
                    for d in guess:
                        current.number_hand.remove(d)
                        game.discard_number.append(d)
                    game.draw_up(current)
                    a, b = game.check_guess(opponent.answer, list(guess))
                    current.socket.sendall(("RESULT %d %d\n" % (a, b)).encode("utf-8"))
                    opponent.socket.sendall(("OPP_GUESS %s %s %d %d\n" %
                                              (current.name, guess, a, b)).encode("utf-8"))

                    if a == game.NUM_GUESS_DIGITS:
                        # 猜中，全部玩家廣播勝利
                        for p in self.players:
                            p.socket.sendall(("WINNER %s\n" % current.name).encode("utf-8"))
                        self.connection_manager.end_event.set()
                        return

            current_round += 1

        # 所有回合跑完，沒人猜中 → 平局
        for p in self.players:
            p.socket.sendall("DRAW\n")
        self.connection_manager.end_event.set()


if __name__ == "__main__":
    HOST, PORT = "0.0.0.0", 12345
    connection_manager = ConnectionManager(HOST, PORT)

    # 用一條 Thread 啟動 listener
    server_thread = threading.Thread(target=connection_manager.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    print(format_log(u"伺服器已啟動 %s:%d" % (HOST, PORT)))

    # 等兩名玩家都連上
    connection_manager.start_event.wait()
    print(format_log(u"兩位玩家已連線，開始遊戲..."))

    game_host = GameHost(connection_manager)
    game_host.run_game()

    # 遊戲結束後關閉 listener 與所有協程
    connection_manager.shutdown()
    server_thread.join(timeout=1)
    print(format_log(u"遊戲結束，伺服器已關閉"))
