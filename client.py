# client.py
# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import socket
import threading
from six.moves import queue
import os
import sys
import uuid

from package.game import Game

try:
    input = raw_input  # Python 2 使用 raw_input
except NameError:
    pass

ENCODING = sys.stdout.encoding or 'utf-8'
ID_FILE = "player_id.txt"

# 嘗試讀取或建立 UUID
if os.path.exists(ID_FILE):
    with open(ID_FILE, "r") as f:
        PLAYER_ID = f.read().strip()
else:
    PLAYER_ID = str(uuid.uuid4())
    with open(ID_FILE, "w") as f:
        f.write(PLAYER_ID)

# 用來在收到需要玩家回覆的指令時，把 prompt 推到這個隊列
prompt_queue = queue.Queue()
guess_histories = list()

def handle_message(msg):
    parts = msg.split()
    cmd = parts[0]

    if cmd == "HAND":
        body = msg[len("HAND "):]
        nums, tools = body.split(";")
        # if os.name == "nt":
        #     os.system("cls")
        # else:
        #     os.system("clear")

        for history in guess_histories:
            print(history)
        print("你的數字手牌:", ",".join(nums.split(",")))
        print("你的道具手牌:", ",".join(tools.split(",")) + "\n")
        return None

    elif cmd == "TOOL":
        choices = [str(c + 1) for c in range(Game.MAX_TOOL_HAND)]
        choices.append(str(-1))
        prompt_text = "是否使用道具卡？輸入編號或輸入 -1 跳過:\n"
        prompt_queue.put({"type": "TOOL", "prompt": prompt_text, "choices": choices})
        return None

    elif cmd == "USED_TOOL":
        print("你使用了道具:", parts[1], "\n")
        return None

    elif cmd == "POS":
        prompt_text = "請輸入要查看的位置 (1~4)：\n"
        valid = [str(i) for i in range(1, Game.NUM_GUESS_DIGITS + 1)]
        prompt_queue.put({"type": "POS", "prompt": prompt_text, "choices": valid})
        return None

    elif cmd == "POS_RESULT":
        msg = "位置 %s 的數字是 %s\n" % (parts[1], parts[2])
        guess_histories.append(msg)
        print(msg)
        return None

    elif cmd == "SHUFFLE_RESULT":
        msg = "打亂對方答案數字: %s\n" % parts[1]
        guess_histories.append(msg)
        print(msg)
        return None

    elif cmd == "EXCLUDE_RESULT":
        print("數字 %s 不在對方答案中\n" % parts[1])
        return None

    elif cmd == "DOUBLE_ACTIVE":
        print("雙重猜測已啟動，本回合可猜兩次\n")
        return None

    elif cmd == "RESHUFFLE_DONE":
        print("已經重洗數字手牌\n")
        return None

    elif cmd == "GUESS":
        number_hand = parts[1]
        prompt_text = "請輸入猜測 (連續輸 4 位數字):\n"
        prompt_queue.put({"type": "GUESS", "prompt": prompt_text, "number_hand": number_hand})
        return None

    elif cmd == "RESULT":
        print("你的結果: %sA%sB\n" % (parts[1], parts[2]))
        guess_histories[-1] += "%sA%sB" % (parts[1], parts[2])
        return None

    elif cmd == "OPP_TOOL":
        msg = "%s 使用了 %s\n" % (parts[1], parts[2])
        print(msg)
        if parts[2] == "SHUFFLE":
            guess_histories.append(msg)
        return None

    elif cmd == "OPP_GUESS":
        print("%s 猜了 %s => %sA%sB\n" % (parts[1], parts[2], parts[3], parts[4]))
        return None

    elif cmd == "WINNER":
        print("遊戲結束，勝利者：", parts[1], "\n")
        if os.path.exists(ID_FILE):
            os.remove(ID_FILE)
        return str("exit")

    elif cmd == "DRAW":
        print("遊戲結束，平局！\n")
        if os.path.exists(ID_FILE):
            os.remove(ID_FILE)
        return str("exit")

    elif cmd == "DISCONNECTED":
        print("%s 失去連線...\n" % parts[1])
        return None

    elif cmd == "HEARTBEAT":
        return str("HEARTBEAT_ACK")

    elif cmd == "STATUS":
        print("等待 {} 使用道具跟猜測中...\n".format(parts[1]))
        return None

    elif cmd == "CHECK_ID":
        return PLAYER_ID

    elif cmd == "FULL":
        print("房間人數已滿~\n")
        return None

    else:
        print(msg + "\n")
        return None


def recv_and_handle(client_socket):
    _buffer = ""
    while True:
        try:
            data = client_socket.recv(1024).decode("utf-8")
        except Exception as e:
            err_no, raw_msg = e.args
            readable = raw_msg.decode('cp950', errors='replace')

            print("Errno %d: %s" % (err_no, readable))
            print("與伺服器連線異常，結束")
            break

        if not data:
            print("伺服器已關閉連線")
            break

        _buffer += data
        while "\n" in _buffer:
            line, _buffer = _buffer.split("\n", 1)
            text = line
            if not text:
                continue
            reply = handle_message(text)
            if isinstance(reply, str):
                send_text = reply + "\n"
                try:
                    client_socket.sendall(send_text.encode("utf-8"))
                except Exception:
                    print("回覆伺服器失敗，結束")
                    return
                if reply == "exit":
                    client_socket.close()
                    raise SystemExit

def prompt_loop(client_socket):
    while True:
        item = prompt_queue.get()
        ptype = item.get("type")

        prompt_text = item["prompt"].encode(sys.stdout.encoding or 'utf-8', 'replace')

        if ptype == "TOOL":
            choices = item["choices"]
            while True:
                choice = input(prompt_text).strip()
                if choice in choices:
                    break
                print("輸入不在選項內，請重新輸入。")
            send = choice + "\n"
            try:
                client_socket.sendall(send.encode("utf-8"))
            except Exception:
                print("傳送選擇失敗，結束")
                return

        elif ptype == "POS":
            choices = item["choices"]
            while True:
                pos = input(prompt_text).strip()
                if pos in choices:
                    break
                print("輸入不合法，請輸入 1~4 之間的整數。")
            send = pos + "\n"
            try:
                client_socket.sendall(send.encode("utf-8"))
            except Exception:
                print("傳送 POS 失敗，結束")
                return

        elif ptype == "GUESS":
            number_hand = item["number_hand"]
            while True:
                guess = list(input(prompt_text).strip())
                if len(guess) != Game.NUM_GUESS_DIGITS:
                    print("長度錯誤，請重新輸入。")
                    continue
                if any(d not in number_hand for d in guess):
                    print("有數字不在手牌中，請重新輸入。")
                    continue
                break
            guess_str = "".join(guess) + "\n"
            try:
                guess_histories.append("%s => " % guess_str[:-1])
                client_socket.sendall(guess_str.encode("utf-8"))
            except Exception:
                print("傳送猜測失敗，結束")
                return
        elif ptype == "exit":
            return
        else:
            continue

if __name__ == "__main__":
    HOST, PORT = "localhost", 12345
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect((HOST, PORT))
        print("伺服器建立連線成功…")

        t_recv = threading.Thread(target=recv_and_handle, args=(sock,))
        t_recv.start()

        t_input = threading.Thread(target=prompt_loop, args=(sock,))
        t_input.start()

        t_recv.join()
        t_input.join()

    except Exception:
        print("與伺服器連線異常…")
    finally:
        sock.close()
        print("連線已關閉")
