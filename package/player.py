import random

class Player(object):
    def __init__(self, name):
        self.name = name
        self.answer = random.sample(list('0123456789'), 4)
        self.number_hand = []   # players draw number cards
        self.tool_hand = []     # players draw tool cards
        self.best_A = 0
        self.best_B = 0
        self.socket = None
        self.guess_histories = []
    def __str__(self):
        return self.name

    def set_socket(self, socket):
        self.socket = socket
