# client.py
# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import socket
import gevent
from gevent.queue import Queue

from package.game import Game

try:
    input = raw_input  # Python 2 使用 raw_input
except NameError:
    pass

# 用來在收到需要玩家回覆的指令時，把 prompt 推到這個隊列
prompt_queue = Queue()


def handle_message(msg):
    """
    處理從伺服器收到的一行文字，並決定是否要推送給 prompt_queue 進行使用者輸入。
    回傳 None 代表不需要立即回覆；如果需要立即回覆（心跳 ACK），直接 return 要發送的字串（含末尾不含 '\n'）。
    """
    parts = msg.split()
    cmd = parts[0]

    if cmd == "HAND":
        # 格式：HAND nums;tools
        body = msg[len("HAND "):]
        nums, tools = body.split(";")
        print("你的數字手牌:", ",".join(nums.split(",")))
        print("你的道具手牌:", ",".join(tools.split(",")) + "\n")
        return None

    elif cmd == "TOOL":
        # 推送一個「道具選擇」prompt 到 prompt_queue
        # choices 範圍 1~Game.MAX_TOOL_HAND 及 -1
        choices = [str(c + 1) for c in range(Game.MAX_TOOL_HAND)]
        choices.append(str(-1))
        prompt_text = "是否使用道具卡？輸入編號或輸入 -1 跳過:\n"
        prompt_queue.put({"type": "TOOL", "prompt": prompt_text, "choices": choices})
        return None

    elif cmd == "USED_TOOL":
        print("你使用了道具:", parts[1], "\n")
        return None

    elif cmd == "POS":
        # 推送一個「POS 查詢」prompt 到 prompt_queue
        prompt_text = "請輸入要查看的位置 (1~4)：\n"
        valid = [str(i) for i in range(1, Game.NUM_GUESS_DIGITS + 1)]
        prompt_queue.put({"type": "POS", "prompt": prompt_text, "choices": valid})
        return None

    elif cmd == "POS_RESULT":
        print("位置 %s 的數字是 %s\n" % (parts[1], parts[2]))
        return None

    elif cmd == "SHUFFLE_RESULT":
        print("打亂對方答案數字:", parts[1] + "\n")
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
        # 推送一個「猜數字」prompt 到 prompt_queue
        number_hand = parts[1]
        prompt_text = "請輸入猜測 (連續輸 4 位數字):\n"
        # valid 不用預先檢查所有組合，只檢查長度和內容在手牌裡
        prompt_queue.put({"type": "GUESS", "prompt": prompt_text, "number_hand": number_hand})
        return None

    elif cmd == "RESULT":
        print("你的結果: %sA%sB\n" % (parts[1], parts[2]))
        return None

    elif cmd == "OPP_TOOL":
        print("%s 使用了 %s\n" % (parts[1], parts[2]))
        return None

    elif cmd == "OPP_GUESS":
        print("%s 猜了 %s => %sA%sB\n" % (parts[1], parts[2], parts[3], parts[4]))
        return None

    elif cmd == "WINNER":
        print("遊戲結束，勝利者：", parts[1], "\n")
        return str("exit")

    elif cmd == "DRAW":
        print("遊戲結束，平局！\n")
        return str("exit")

    elif cmd == "DISCONNECTED":
        print("%s 失去連線...\n" % parts[1])
        return None

    elif cmd == "HEARTBEAT":
        return str("HEARTBEAT_ACK")

    elif cmd == "STATUS":
        print("等待 {} 使用道具跟猜測中...\n".format(parts[1]))
        return None

    elif cmd == "FULL":
        print("房間人數已滿~\n")
        return None

    else:
        # 其餘訊息
        print(msg + "\n")
        return None


def recv_and_handle(client_socket):
    """
    獨立綠線程：不斷從 socket.recv() 讀取伺服器訊息，
    拆行處理，並在需要立刻回覆時馬上 sendall()。
    其它需要使用者輸入的指令會推到 prompt_queue。
    """
    buffer = b""
    while True:
        try:
            data = client_socket.recv(1024).decode("utf-8")
        except Exception:
            print("與伺服器連線異常，結束")
            break

        if not data:
            print("伺服器已關閉連線")
            break

        buffer += data
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            text = line.decode("utf-8").strip()
            if not text:
                continue
            # print(text)
            reply = handle_message(text)
            if isinstance(reply, str):
                send_text = reply + "\n"
                try:
                    client_socket.sendall(send_text.encode("utf-8"))
                except Exception:
                    print("回覆伺服器失敗，結束")
                    return
                if reply == "exit":
                    # 收到 exit 指示後直接結束程式
                    client_socket.close()
                    raise SystemExit


def prompt_loop(client_socket):
    """
    獨立綠線程：專門等候 prompt_queue 中的提示，觸發 input() 取得使用者輸入後 sendall() 到伺服器。
    這樣可以讓 recv_and_handle() 完全不被 input() 阻塞，確保心跳或其它訊息隨時能被處理。
    """
    while True:
        item = prompt_queue.get()  # 這裡會等待，直到有要使用者回覆的 prompt
        ptype = item.get("type")

        if ptype == "TOOL":
            prompt_text = item["prompt"]
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
            prompt_text = item["prompt"]
            valid = item["choices"]  # e.g. ["1","2","3","4"]
            while True:
                pos = input(prompt_text).strip()
                if pos in valid:
                    break
                print("輸入不合法，請輸入 1~4 之間的整數。")
            send = pos + "\n"
            try:
                client_socket.sendall(send.encode("utf-8"))
            except Exception:
                print("傳送 POS 失敗，結束")
                return

        elif ptype == "GUESS":
            prompt_text = item["prompt"]
            number_hand = item["number_hand"]  # e.g. "1234"
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
                client_socket.sendall(guess_str.encode("utf-8"))
            except Exception:
                print("傳送猜測失敗，結束")
                return

        elif ptype == "exit":
            # 遊戲已經結束，由 recv_and_handle 發現並關閉 socket
            return

        else:
            # 若有其他未預期的 prompt type，可在此處理
            continue


if __name__ == "__main__":
    HOST, PORT = "localhost", 12345
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client_socket.connect((HOST, PORT))
        print("伺服器建立連線成功…")

        # 啟動兩個並行綠線程：
        # 1. recv_and_handle：專門負責讀伺服器訊息並 dispatch
        # 2. prompt_loop     ：專門負責「需要使用者輸入時」的互動
        gevent.joinall([
            gevent.spawn(recv_and_handle, client_socket),
            gevent.spawn(prompt_loop, client_socket),
        ])

    except Exception:
        print("與伺服器連線異常…")
    finally:
        client_socket.close()
        print("連線已關閉")
