# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import socket
import sys

try:
    input = raw_input  # Python 2 使用 raw_input
except NameError:
    pass

def handle_message(msg):
    parts = msg.split()
    cmd = parts[0]

    if cmd == "HAND":
        # 格式：HAND nums;tools
        body = msg[len("HAND "):]
        nums, tools = body.split(";")
        print("你的數字手牌:", ",".join(nums.split(",")))
        print("你的道具手牌:", ",".join(tools.split(",")))
    elif cmd == "TOOL?":
        return input("是否使用道具卡？輸入編號或輸入 -1 跳過: ").strip()
    elif cmd == "USED_TOOL":
        print("你使用了道具:", parts[1])
    elif cmd == "POS?":
        return input("請輸入要查看的位置 (1~4)：").strip()
    elif cmd == "POS_RESULT":
        print("位置 %s 的數字是 %s" % (parts[1], parts[2]))
    elif cmd == "SHUFFLE_RESULT":
        print("打亂對方答案數字:", parts[1])
    elif cmd == "EXCLUDE_RESULT":
        print("證明 %s 不在對方答案中" % parts[1])
    elif cmd == "DOUBLE_ACTIVE":
        print("雙重猜測已啟動，本回合可猜兩次")
    elif cmd == "RESHUFFLE_DONE":
        print("已經重洗數字手牌\n")
    elif cmd == "GUESS?":
        return input("請輸入猜測 (連續輸 4 位數字): ").strip()
    elif cmd == "RESULT":
        print("你的結果: %sA%sB" % (parts[1], parts[2]))
    elif cmd == "OPP_GUESS":
        print("對手猜了 %s => %sA%sB" % (parts[1], parts[2], parts[3]))
    elif cmd == "WINNER":
        print("遊戲結束，勝利者：", parts[1])
        return "exit"
    elif cmd == "DRAW":
        print("遊戲結束，平局！")
        return "exit"
    elif cmd == "STATUS":
        print("等待{}使用道具跟猜測中...\n".format(parts[1]))
    else:
        # 其餘訊息
        print(msg)
    return None

try:
    HOST, PORT = "localhost", 12345
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print("伺服器建立連線成功...")
    while True:
        data = sock.recv(1024)
        if not data:
            break
        text = data.decode("utf-8").strip()
        # 伺服器常用多行廣播，逐行解析
        for line in text.split("\n"):
            if not line:
                continue
            reply = handle_message(line)
            if reply is not None:
                sock.sendall(reply.encode("utf-8"))
                if reply == "exit":
                    raise SystemExit
finally:
    sock.close()
    print("連線已關閉")
