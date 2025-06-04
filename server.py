# -*- coding: utf-8 -*-
from __future__ import print_function
import threading

try:
    import SocketServer  # Python 2
except ImportError:
    import socketserver as SocketServer  # Python 3

from package.player import Player
from package.game import Game, ToolCard

players = []            # 存放玩家連線的 socket 物件
start_game = threading.Event()
end_game = threading.Event()

class TCPHandler(SocketServer.BaseRequestHandler):
    def handle(self):
        player = Player(u"玩家{}".format(len(players)+1))
        player.set_socket(self.request)
        players.append(player)
        print(u"{}已連線".format(player.name))
        if len(players) == 2:
            start_game.set()
        # 等待遊戲結束
        end_game.wait()

def broadcast(msg, skip_players=None):
    """發送 UTF-8 編碼的 msg 給除了skip_players索引的玩家"""
    global players
    for i in range(len(players)):
        if skip_players and i in skip_players:
            continue
        players[i].socket.sendall(msg.encode('utf-8'))

def send_to(player, msg):
    try:
        player.socket.sendall(msg.encode('utf-8'))
    except Exception:
        # 這個連線已經無效，移除並關閉
        try:
            player.socket.close()
        except:
            pass

def recv_from(player):
    try:
        data = player.socket.recv(1024)
        if not data:
            return ""
        return data.decode('utf-8').strip()
    except Exception:
        return ""


def run_game():
    global players
    game = Game(players)

    for idx in range(1, len(players)):
        hand_nums = ",".join(players[idx].number_hand)
        hand_tools = ",".join(players[idx].tool_hand)
        send_to(players[idx], "HAND %s;%s\n" % (hand_nums, hand_tools))

    while game.current_round <= game.MAX_ROUNDS:
        for idx in [0, 1]:
            if idx >= len(players):
                end_game.set()
                return

            hand_nums = ",".join(players[idx].number_hand)
            hand_tools = ",".join(players[idx].tool_hand)
            send_to(players[idx], "HAND %s;%s\n" % (hand_nums, hand_tools))

            current = players[idx]
            opponent = players[1 - idx]

            broadcast(u"STATUS %s\n" % current.name, skip_players=[idx])

            send_to(current, "TOOL\n")
            choice = recv_from(current)
            if choice is None:
                broadcast(u"WINNER %s\n" % opponent.name)
                end_game.set()
                return

            extra_guess = False

            if choice.isdigit():
                ci = int(choice) - 1
                if 0 <= ci < len(current.tool_hand):
                    tool = current.tool_hand.pop(ci)
                    game.discard_tool.append(tool)

                    send_to(current, "USED_TOOL %s\n" % tool)
                    broadcast(u"OPP_TOOL %s %s\n" % (current.name, tool), skip_players=[idx])

                    # 不同道具對應的流程
                    if tool == "POS":
                        send_to(current, "POS\n")
                        pos_str = recv_from(current)
                        if pos_str is None:
                            broadcast("WINNER %s\n" % opponent.name)
                            end_game.set()
                            return

                        while not (pos_str.isdigit() and 1 <= int(pos_str) <= game.NUM_GUESS_DIGITS):
                            send_to(current, "POS\n")
                            pos_str = recv_from(current)
                            if pos_str is None:
                                broadcast("WINNER %s\n" % opponent.name)
                                end_game.set()
                                return
                        pi = int(pos_str)
                        digit = ToolCard.pos(opponent.answer, pi)
                        send_to(current, "POS_RESULT %d %s\n" % (pi, digit))

                    elif tool == "SHUFFLE":
                        ToolCard.shuffle(current.answer)
                        send_to(current, "SHUFFLE_RESULT %s\n" % "".join(current.answer))

                    elif tool == "EXCLUDE":
                        exclude_result = ToolCard.exclude(opponent.answer)
                        send_to(current, "EXCLUDE_RESULT %s\n" % exclude_result)

                    elif tool == "DOUBLE":
                        extra_guess = True
                        send_to(current, "DOUBLE_ACTIVE\n")

                    elif tool == "RESHUFFLE":
                        ToolCard.reshuffle(current.number_hand, game.number_deck)
                        send_to(current, "RESHUFFLE_DONE\n")
                else:
                    pass

            guesses = 2 if extra_guess else 1
            for _ in range(guesses):
                # 顯示手牌
                nums = ",".join(current.number_hand)
                tools = ",".join(current.tool_hand)
                send_to(current, "HAND %s;%s\n" % (nums, tools))

                send_to(current, "GUESS %s\n" % hand_nums)
                guess = recv_from(current)
                if guess is None:
                    broadcast("WINNER %s\n" % opponent.name)
                    end_game.set()
                    return

                for number_card in guess:
                    current.number_hand.remove(number_card)
                    game.discard_number.append(number_card)

                game.draw_up(current)
                a, b = game.check_guess(opponent.answer, list(guess))
                send_to(current, "RESULT %d %d\n" % (a, b))
                send_to(players[1-idx], "OPP_GUESS %s %s %d %d\n" % (current, guess, a, b))

                if a == game.NUM_GUESS_DIGITS:
                    broadcast("WINNER %s\n" % current.name)
                    end_game.set()
                    return

        game.current_round += 1

if __name__ == "__main__":
    HOST, PORT = "0.0.0.0", 12345
    server = SocketServer.ThreadingTCPServer((HOST, PORT), TCPHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    print("伺服器已啟動 %s:%d" % (HOST, PORT))

    # 等兩名玩家連線後開始遊戲
    start_game.wait()
    print("兩位玩家已連線，開始遊戲...")
    run_game()

    server.shutdown()
    server.server_close()
    print("伺服器已關閉")
