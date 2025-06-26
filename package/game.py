# -*- coding: utf-8 -*-
from __future__ import print_function
import random
import sys

from package.player import Player

try:
    input = raw_input
except NameError:
    pass

class Game(object):
    NUM_CARD_COPIES = 4
    TOOL_CARDS = {
        'POS': 2,
        'SHUFFLE': 2,
        'EXCLUDE': 2,
        'DOUBLE': 1,
        'RESHUFFLE': 1,
    }
    MAX_NUM_HAND = 8
    MAX_TOOL_HAND = 3
    MAX_ROUNDS = 10
    NUM_GUESS_DIGITS = 4

    def __init__(self, players):
        self.discard_tool = None
        self.discard_number = None
        self.tool_deck = None
        self.number_deck = None
        
        self.players = players
        self.round = 1
        self.current_player_idx = 0
        self.build_decks()
        self.deal_initial_hands()

    def build_decks(self):
        # 建立數字牌堆與道具牌堆
        self.number_deck = [d for d in '0123456789' for _ in range(self.NUM_CARD_COPIES)]
        self.tool_deck = [t for t, n in self.TOOL_CARDS.items() for _ in range(n)]
        random.shuffle(self.number_deck)
        random.shuffle(self.tool_deck)
        self.discard_number = []
        self.discard_tool = []

    def deal_initial_hands(self):
        for player in self.players:
            player.answer = random.sample(list('0123456789'), self.NUM_GUESS_DIGITS)  # 隱藏答案
            player.number_hand = []
            player.tool_hand = []
            player.best_A = 0
            player.best_B = 0
            self.draw_up(player)  # 補牌

    @staticmethod
    def draw(hand, deck, discard, max_hand):
        while len(hand) < max_hand:
            if not deck:
                if not discard:
                    break
                deck.extend(discard)
                del discard[:]
                random.shuffle(deck)
            hand.append(deck.pop())
        hand.sort()

    def draw_up(self, player):
        Game.draw(player.number_hand, self.number_deck, self.discard_number, self.MAX_NUM_HAND)
        Game.draw(player.tool_hand, self.tool_deck, self.discard_tool, self.MAX_TOOL_HAND)

    @staticmethod
    def check_guess(answer, guess):
        # 計算 A 與 B 的數量
        a = sum(a == g for a, g in zip(answer, guess))
        b = len(set(answer) & set(guess)) - a
        return a, b

    def to_dict(self):
        """
        將 Game 物件轉換為可儲存到 Redis 的 dict 格式。

        :return: dict 包含遊戲當前狀態，包括牌堆、棄牌堆、回合數、參數設定、所有玩家資訊。
                 - number_deck: 剩餘數字卡牌堆
                 - tool_deck: 剩餘道具卡牌堆
                 - discard_number: 已棄用的數字卡牌
                 - discard_tool: 已棄用的道具卡牌
                 - round: 當前遊戲進行到的回合數
                 - MAX_ROUNDS: 遊戲總回合數上限
                 - NUM_GUESS_DIGITS: 每次猜測的數字長度
                 - players: 玩家狀態清單（每個 player 會呼叫其自身的 to_dict()）
        """
        return {
            "number_deck": self.number_deck,
            "tool_deck": self.tool_deck,
            "discard_number": self.discard_number,
            "discard_tool": self.discard_tool,
            "round": self.round,
            "MAX_ROUNDS": self.MAX_ROUNDS,
            "NUM_GUESS_DIGITS": self.NUM_GUESS_DIGITS,
            "players": [p.to_dict() for p in self.players],
        }

    @classmethod
    def from_dict(cls, state_dict, players=None):

        if players is None:
            if state_dict['players']:
                players = [Player.from_dict(p) for p in state_dict['players']]
            else:
                raise Exception('No players specified')

        game = cls(players)
        game.number_deck = state_dict.get("number_deck", [])
        game.tool_deck = state_dict.get("tool_deck", [])
        game.discard_number = state_dict.get("discard_number", [])
        game.discard_tool = state_dict.get("discard_tool", [])
        game.round = state_dict.get("round", 1)
        game.MAX_ROUNDS = state_dict.get("MAX_ROUNDS", Game.MAX_ROUNDS)
        game.NUM_GUESS_DIGITS = state_dict.get("NUM_GUESS_DIGITS", Game.NUM_GUESS_DIGITS)
        return game

    # def apply_tool(self, player, opponent):
    #     if not player.tool_hand:
    #         print("沒有道具卡可使用。")
    #         return False, False
    #
    #     print("可用道具:")
    #     for idx, card in enumerate(player.tool_hand):
    #         print("[{0}] {1}".format(idx + 1, card))
    #
    #     choice = input("輸入道具卡編號（1~{}），或直接 Enter 跳過：".format(len(player.tool_hand)))
    #     if not choice.isdigit():
    #         return False, False
    #     idx = int(choice) - 1
    #     if idx < 0 or idx >= len(player.tool_hand):
    #         print("輸入超出範圍。")
    #         return False, False
    #
    #     selected = player.tool_hand.pop(idx)
    #     self.discard_tool.append(selected)
    #     extra_guess = False
    #
    #     print("{0} 使用道具: {1}".format(player.name, selected))
    #
    #     if selected == 'POS':
    #         ToolCard.pos(opponent.answer)
    #
    #     elif selected == 'SHUFFLE':
    #         ToolCard.shuffle(opponent.answer)
    #
    #     elif selected == 'EXCLUDE':
    #         ToolCard.exclude(opponent.answer)
    #
    #     elif selected == 'DOUBLE':
    #         extra_guess = True
    #         print("本回合可進行雙重猜測！")
    #
    #     elif selected == 'RESHUFFLE':
    #         ToolCard.reshuffle(player.number_hand, self.number_deck)
    #
    #     return True, extra_guess

    # def play_turn(self, player, opponent):
    #     print("=== 回合 {0}：{1} 的回合 ===".format(self.current_round, player.name))
    #     self.draw_up(player)  # 補牌
    #     print("數字手牌：", player.number_hand)
    #     print("道具手牌：", player.tool_hand)
    #
    #     used_tool, double = self.apply_tool(player, opponent)
    #     guesses = 2 if double else 1
    #
    #     for i in range(guesses):
    #         print("-- 猜測 {} --".format(i + 1))
    #         self.draw_up(player.number_hand)
    #
    #         guess = []
    #         while not guess:
    #             guess = list(input("輸入 {} 張手牌（連續輸入，不含空格）：".format(self.NUM_GUESS_DIGITS)).strip())
    #             if len(guess) != self.NUM_GUESS_DIGITS:
    #                 print("長度錯誤，請重新輸入。")
    #                 guess = []
    #                 continue
    #             if any(d not in player.number_hand for d in guess):
    #                 print("有數字不在手牌中，請重新輸入。")
    #                 guess = []
    #                 continue
    #
    #         for card in guess:
    #             player.number_hand.remove(card)
    #             self.discard_number.append(card)
    #
    #         a, b = self.check_guess(opponent.answer, guess)
    #         print("{0}A{1}B".format(a, b))
    #
    #         if a > player.best_A:
    #             player.best_A = a
    #             player.best_B = b
    #         elif a == player.best_A and b > player.best_B:
    #             player.best_B = b
    #
    #         if a == self.NUM_GUESS_DIGITS:
    #             print("{0} 猜中對手答案，立即勝利！".format(player.name))
    #             return True
    #
    #     return False

    # def play(self):
    #     print("開始遊戲: 卡牌版 1A2B 對決！")
    #     while self.current_round <= self.MAX_ROUNDS:
    #         player = self.players[self.current_player_idx]
    #         opponent = self.players[1 - self.current_player_idx]
    #         if self.play_turn(player, opponent):
    #             return
    #         self.current_player_idx = 1 - self.current_player_idx
    #         if self.current_player_idx == 0:
    #             self.current_round += 1
    #
    #     # 遊戲結束，用 best A/B 判定勝負
    #     p1, p2 = self.players
    #     print("回合用盡，進行點數比較：")
    #     print("{0}: best_A={1}, best_B={2}".format(p1.name, p1.best_A, p1.best_B))
    #     print("{0}: best_A={1}, best_B={2}".format(p2.name, p2.best_A, p2.best_B))
    #     if p1.best_A != p2.best_A:
    #         winner = p1 if p1.best_A > p2.best_A else p2
    #     elif p1.best_B != p2.best_B:
    #         winner = p1 if p1.best_B > p2.best_B else p2
    #     else:
    #         print("平局！")
    #         return
    #     print("勝利者: {0}！".format(winner.name))

class ToolCard:
    def __init__(self):
        pass

    @staticmethod
    def pos(answer, pos):
        return answer[pos]

    @staticmethod
    def shuffle(answer):
        random.shuffle(answer)

    @staticmethod
    def exclude(answer):
        non_answer = [d for d in '0123456789' if d not in answer]
        if non_answer:
            return random.choice(non_answer)
        else:
            return ''
    @staticmethod
    def reshuffle(number_hand, number_deck):
        n = len(number_hand)
        merged = number_hand + number_deck
        random.shuffle(merged)

        del number_deck[:]
        del number_hand[:]

        number_hand.extend(merged[:n])
        number_deck.extend(merged[n:])

        number_hand.sort()
        number_deck.sort()