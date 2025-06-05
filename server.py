# -*- coding: utf-8 -*-
from __future__ import print_function
import threading

try:
    import SocketServer  # Python 2
except ImportError:
    import socketserver as SocketServer  # Python 3

from package.player import Player
from package.game import Game, ToolCard
from datetime import datetime

def format_log(user, msg):
    log = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    name = user.name if user is isinstance(user, Player) else user
    return "[{}] {} - {}".format(log, name, msg)

class TCPHandler(SocketServer.BaseRequestHandler):
    def handle(self):
        players = self.server.players
        player = Player(u"玩家{}".format(len(players)+1))
        player.set_socket(self.request)
        players.append(player)
        print(u"{}已連線".format(player.name))
        if len(players) == 2:
            self.server.start_game_event.set()
        # 等待遊戲結束
        self.server.end_game_event.wait()

class Server(SocketServer.ThreadingTCPServer):
    def __init__(self, server_address):
        SocketServer.ThreadingTCPServer.__init__(self, server_address, TCPHandler)
        self.players = []
        self.start_game_event = threading.Event()
        self.end_game_event = threading.Event()

    def broadcast(self, msg, skip_players=None):
        """發送 UTF-8 編碼的 msg 給除了skip_players索引的玩家"""
        for i in range(len(self.players)):
            if skip_players and i in skip_players:
                continue
            self.players[i].socket.sendall(msg.encode('utf-8'))

    @staticmethod
    def send_to(player, msg):
        try:
            player.socket.sendall(msg.encode('utf-8'))
        except Exception:
            # 這個連線已經無效，移除並關閉
            try:
                player.socket.close()
            except:
                pass

    @staticmethod
    def recv_from(player):
        try:
            data = player.socket.recv(1024)
            if not data:
                return ""
            return data.decode('utf-8').strip()
        except Exception:
            return ""


    def run_game(self):
        players = self.players
        game = Game(players)

        for idx in range(1, len(players)):
            hand_nums = ",".join(players[idx].number_hand)
            hand_tools = ",".join(players[idx].tool_hand)
            Server.send_to(players[idx], "HAND %s;%s\n" % (hand_nums, hand_tools))

        while game.current_round <= game.MAX_ROUNDS:
            for idx in [0, 1]:
                if idx >= len(players):
                    self.end_game_event.set()
                    return

                hand_nums = ",".join(players[idx].number_hand)
                hand_tools = ",".join(players[idx].tool_hand)
                Server.send_to(players[idx], "HAND %s;%s\n" % (hand_nums, hand_tools))

                current = players[idx]
                opponent = players[1 - idx]

                self.broadcast(u"STATUS %s\n" % current.name, skip_players=[idx])

                Server.send_to(current, "TOOL\n")
                choice = Server.recv_from(current)
                if choice is None:
                    self.broadcast(u"WINNER %s\n" % opponent.name)
                    self.end_game_event.set()
                    return

                extra_guess = False

                if choice.isdigit():
                    ci = int(choice) - 1
                    if 0 <= ci < len(current.tool_hand):
                        tool = current.tool_hand.pop(ci)
                        print(format_log(current, "使用 {}".format(tool)))
                        game.discard_tool.append(tool)

                        Server.send_to(current, "USED_TOOL %s\n" % tool)
                        self.broadcast(u"OPP_TOOL %s %s\n" % (current.name, tool), skip_players=[idx])

                        # 不同道具對應的流程
                        if tool == "POS":
                            Server.send_to(current, "POS\n")
                            pos_str = Server.recv_from(current)
                            if pos_str is None:
                                self.broadcast("WINNER %s\n" % opponent.name)
                                self.end_game_event.set()
                                return

                            while not (pos_str.isdigit() and 1 <= int(pos_str) <= game.NUM_GUESS_DIGITS):
                                Server.send_to(current, "POS\n")
                                pos_str = Server.recv_from(current)
                                print(format_log(current, pos_str))
                                if pos_str is None:
                                    self.broadcast("WINNER %s\n" % opponent.name)
                                    self.end_game_event.set()
                                    return
                            pi = int(pos_str)
                            digit = ToolCard.pos(opponent.answer, pi)
                            Server.send_to(current, "POS_RESULT %d %s\n" % (pi, digit))

                        elif tool == "SHUFFLE":
                            ToolCard.shuffle(current.answer)
                            Server.send_to(current, "SHUFFLE_RESULT %s\n" % "".join(current.answer))

                        elif tool == "EXCLUDE":
                            exclude_result = ToolCard.exclude(opponent.answer)
                            Server.send_to(current, "EXCLUDE_RESULT %s\n" % exclude_result)

                        elif tool == "DOUBLE":
                            extra_guess = True
                            Server.send_to(current, "DOUBLE_ACTIVE\n")

                        elif tool == "RESHUFFLE":
                            ToolCard.reshuffle(current.number_hand, game.number_deck)
                            Server.send_to(current, "RESHUFFLE_DONE\n")
                    else:
                        pass

                guesses = 2 if extra_guess else 1
                for _ in range(guesses):
                    # 顯示手牌
                    nums = ",".join(current.number_hand)
                    tools = ",".join(current.tool_hand)
                    Server.send_to(current, "HAND %s;%s\n" % (nums, tools))

                    Server.send_to(current, "GUESS %s\n" % hand_nums)
                    guess = Server.recv_from(current)
                    print(format_log(current, "猜了 {}".format(guess)))

                    if guess is None:
                        self.broadcast("WINNER %s\n" % opponent.name)
                        self.end_game_event.set()
                        return

                    for number_card in guess:
                        current.number_hand.remove(number_card)
                        game.discard_number.append(number_card)

                    game.draw_up(current)
                    a, b = game.check_guess(opponent.answer, list(guess))
                    Server.send_to(current, "RESULT %d %d\n" % (a, b))
                    Server.send_to(players[1-idx], "OPP_GUESS %s %s %d %d\n" % (current, guess, a, b))

                    if a == game.NUM_GUESS_DIGITS:
                        self.broadcast("WINNER %s\n" % current.name)
                        self.end_game_event.set()
                        return

            game.current_round += 1

if __name__ == "__main__":
    HOST, PORT = "0.0.0.0", 12345
    server = Server((HOST, PORT))
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    print("伺服器已啟動 %s:%d" % (HOST, PORT))

    # 等兩名玩家連線後開始遊戲
    server.start_game_event.wait()
    print("兩位玩家已連線，開始遊戲...")
    server.run_game()

    server.shutdown()
    server.server_close()
    print("伺服器已關閉")
