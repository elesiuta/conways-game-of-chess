#!/usr/bin/env python3
# Conway's Game of Chess
# Copyright (C) 2023 Eric Lesiuta

import argparse
import atexit
import curses
import hashlib
import os
import pickle
import textwrap
import time
import socket
import sys


def exit_handler(engine, engine_state, conn, *args) -> None:
    """clean up in the event of an exception and atexit functions aren't called"""
    type_, value, traceback = args
    print(type_, value, traceback, file=sys.stderr)
    print(" ".join(engine.recent_moves_str))
    with open(engine.args.save, "wb") as f:
        pickle.dump(engine_state, f)
    if conn:
        conn.close()

def start_cli() -> int:
    # cli
    parser = argparse.ArgumentParser(description="Conway's game of chess")
    parser.epilog = textwrap.dedent("""
    Conway's game of chess is a chess variant where the pieces can reproduce and die.
    Legend:             White birth queue ┐
    White: P R P N P B P Q P B P N P R  <─┘
    ┌──────────────────────────────────────┐
    │ # <─ White birth COUNTER on empty  w │
    │      squares, born from queue on   ^ │
    │      next turn after reaching 2    │ │
    │                                    │ │
    │   INDICATOR that white has exactly ┘ │
    │   3 nearby pieces, birth counter     │
    │   will increment at the start of the │
    │   next turn, black birth counter and │
    │   indicator are below and separate   │
    │                                      │
    │ #               ♔ <─ Piece symbol  o │
    │ ^                                  ^ │
    │ └ Death COUNTER on occupied        │ │
    │   squares, dies after reaching 3   │ │
    │                                    │ │
    │   INDICATOR that the piece has > 3 ┘ │
    │   nearby pieces (overpopulation),    │
    │   or < 2 nearby pieces               │
    │   (underpopulation) and will die     │
    │                                      │
    │ # <─ Black COUNTER & INDICATOR ──> l │
    └──────────────────────────────────────┘
    The INDICATORs are updated immediately when the conditions are met.
    The COUNTERs are incremented only at the start of the respective player's turn.
    Births and deaths also only occur at the start of the respective player's turn.
    If the conditions for a birth or death counter are no longer met, (as shown by the indicators), the counter resets.
    Opponent pieces are not counted as nearby pieces for the birth/death population criteria.
    On birth, pieces are taken from the birth queue (circular) and placed on the board in order of rank then file.
    Placement starts from rank 1 for white and rank 8 for black, with both filling the board from left to right.
    The game ends when the king is captured or perishes due to over/underpopulation.
    """)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("--flip", action="store_true", help="flip the board")
    parser.add_argument("--save", action="store", metavar="FILE", default="conway_chess.pickle", help="save file location")
    parser.add_argument("--load", action="store", metavar="FILE", help="load a save file")
    parser.add_argument("--host", nargs=3, metavar=("HOST", "PORT", "COLOR"), help="host a game")
    parser.add_argument("--join", nargs=2, metavar=("HOST", "PORT"), help="join a game")
    parser.add_argument("--ascii", action="store_true", help="use ascii characters for pieces")
    parser.add_argument("--light", action="store_true", help="flip unicode piece colors for light terminals")
    args = parser.parse_args()
    # print instructions before playing
    print(parser.epilog)
    _ = input("Press enter to play")
    # networking
    conn, my_colour = None, None
    if args.host and args.join:
        print("You can only host or join a game, not both", file=sys.stderr)
        return 1
    if args.host:
        if args.host[2] not in ("white", "black"):
            print("You can only host as white or black", file=sys.stderr)
            return 1
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # s = ssl._create_unverified_context().wrap_socket(s, server_side=True)
        s.bind((args.host[0], int(args.host[1])))
        s.listen()
        conn, addr = s.accept()
        conn.sendall(args.host[2].encode())
        my_colour = args.host[2]
        atexit.register(conn.close)
    elif args.join:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # conn = ssl._create_unverified_context().wrap_socket(conn, server_side=False)
        conn.connect((args.join[0], int(args.join[1])))
        host_colour = conn.recv(5).decode()
        if host_colour == "white":
            my_colour = "black"
        else:
            my_colour = "white"
        atexit.register(conn.close)
    # engine initialization
    engine = Engine(args)
    engine_state = [pickle.dumps(engine)]
    engine_state_redo = []
    if args.load:
        engine_state = pickle.load(open(args.load, "rb"))
        for i in range(len(engine_state)):
            engine_state[i] = pickle.loads(engine_state[i])
            engine_state[i].args = args
            engine_state[i] = pickle.dumps(engine_state[i])
        engine = pickle.loads(engine_state[-1])
    atexit.register(lambda: print(" ".join(engine.recent_moves_str)))
    atexit.register(lambda: pickle.dump(engine_state, open(args.save, "wb")))
    sys.excepthook = lambda *args: exit_handler(engine, engine_state, conn, *args)
    try:
        from stockfish import Stockfish
        stockfish = Stockfish()
    except:
        stockfish = None
    # check terminal size
    columns, lines = os.get_terminal_size()
    assert engine.height <= lines, f"Terminal height ({lines}) is too short by {engine.height - lines} lines"
    assert engine.width <= columns, f"Terminal width ({columns}) is too narrow by {engine.width - columns} columns"
    # main curses loop
    for err_count in reversed(range(30)):
        try:
            return curses.wrapper(main_loop, engine, engine_state, engine_state_redo, stockfish, conn, my_colour)
        except curses.error as e:
            print("CURSES ERROR: %s" % e, file=sys.stderr)
            print("try resizing your terminal, game will quit in %s seconds" % (err_count + 1), file=sys.stderr)
            time.sleep(1)
        except Exception as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1
    return 1

def main_loop(stdscr, engine: "Engine", engine_state: list[bytes], engine_state_redo: list[bytes], stockfish, conn, my_colour) -> int:
    """main loop for the curses implementation of the game"""
    curses.cbreak()
    curses.noecho()
    while True:
        # refresh screen
        current_display = engine.display(my_colour)
        stdscr.clear()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(2, curses.COLOR_BLUE, curses.COLOR_WHITE)
        curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_WHITE)
        curses.init_pair(5, curses.COLOR_YELLOW, curses.COLOR_BLUE)
        curses.init_pair(6, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(7, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(8, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(9, curses.COLOR_CYAN, curses.COLOR_BLACK)
        stdscr.attrset(curses.color_pair(0))
        y = 0
        for line in current_display:
            for x, char in enumerate(line):
                # pieces
                if char in "RNBQKP" and y > 4 and y < engine.height - 2:
                    if engine.use_unicode:
                        char = engine.unicode_replacements[char]
                    else:
                        if char == "K":
                            stdscr.attrset(curses.color_pair(4))
                        else:
                            stdscr.attrset(curses.color_pair(2))
                elif char in "rnbqkp" and y > 4 and y < engine.height - 2:
                    if engine.use_unicode:
                        char = engine.unicode_replacements[char]
                    else:
                        if char == "k":
                            stdscr.attrset(curses.color_pair(5))
                        else:
                            stdscr.attrset(curses.color_pair(3))
                # indicators
                elif char in "wl" and y > 4 and y < engine.height - 2:
                    stdscr.attrset(curses.color_pair(9))
                elif char in "ou" and y > 4 and y < engine.height - 2:
                    stdscr.attrset(curses.color_pair(7))
                # death counters
                elif char == "0" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 == 2:
                    stdscr.attrset(curses.color_pair(7))
                elif char == "1" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 == 2:
                    stdscr.attrset(curses.color_pair(7))
                elif char == "2" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 == 2:
                    stdscr.attrset(curses.color_pair(7))
                elif char == "3" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 == 2:
                    stdscr.attrset(curses.color_pair(8))
                # birth counters
                elif char == "0" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 != 2:
                    stdscr.attrset(curses.color_pair(9))
                elif char == "1" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 != 2:
                    stdscr.attrset(curses.color_pair(9))
                elif char == "2" and x > 1 and x < engine.width - 1 and y > 4 and y < engine.height - 2 and (y - 5) % 4 != 2:
                    stdscr.attrset(curses.color_pair(6))
                # board
                else:
                    stdscr.attrset(curses.color_pair(0))
                stdscr.addstr(y, x, char)
            y += 1
        stdscr.move(*engine.get_cursor())
        stdscr.refresh()
        # check for key press, sync with network player if connected, and update engine state
        key = None
        if conn:
            if my_colour == engine.current_turn:
                ch: int = stdscr.getch()
                ch = engine.flip_cursor_y(ch, curses.KEY_UP, curses.KEY_DOWN)
                conn.sendall(ch.to_bytes(2, "big") + hashlib.sha256(pickle.dumps(engine.recent_moves_str)).digest()[-2:])
            else:
                msg = conn.recv(4)
                ch = int.from_bytes(msg[:2], "big")
                hash_lsb = msg[2:]
                assert hash_lsb == hashlib.sha256(pickle.dumps(engine.recent_moves_str)).digest()[-2:], f"client and server are out of sync"
        else:
            ch: int = stdscr.getch()
            ch = engine.flip_cursor_y(ch, curses.KEY_UP, curses.KEY_DOWN)
        if ch == ord("\n") or ch == ord(" "):
            key = "enter"
        elif ch == ord("s"):
            key = "stockfish"
        elif ch == curses.KEY_UP:
            key = "up"
        elif ch == curses.KEY_DOWN:
            key = "down"
        elif ch == curses.KEY_LEFT:
            key = "left"
        elif ch == curses.KEY_RIGHT:
            key = "right"
        elif ch == curses.KEY_BACKSPACE or ch == ord("u"):
            if len(engine_state) >= 2:
                engine_state_redo.append(engine_state.pop())
                engine = pickle.loads(engine_state[-1])
            continue
        elif ch == ord("r"):
            if engine_state_redo:
                engine_state.append(engine_state_redo.pop())
                engine = pickle.loads(engine_state[-1])
            continue
        elif ch == 27 or ch == ord("q"):
            key = "esc"
            return 0
        else:
            key = "other"
        if engine.update_state(key, stockfish):
            engine_state.append(pickle.dumps(engine))
            engine_state_redo = []


class Engine:
    def __init__(self, args) -> None:
        self.args = args
        self.board = Board(self.args)
        # tick all the pieces for the first turn
        for piece in self.board.get_pieces():
            piece.tick(self.board.get_surrounding_pieces(piece), "white", True)
        self.cursor_row = 0
        self.cursor_col = 0
        self.height = len(self.board.display()) + 5
        self.width = len(self.board.display()[0])
        self.white_birth_queue = ["P", "R", "P", "N", "P", "B", "P", "Q", "P", "B", "P", "N", "P", "R"]
        self.black_birth_queue = ["P", "R", "P", "N", "P", "B", "P", "Q", "P", "B", "P", "N", "P", "R"]
        self.selected_piece = None
        self.current_turn = "white"
        self.col_labels = ["a", "b", "c", "d", "e", "f", "g", "h"]
        self.recent_moves = []
        self.recent_moves_str = []
        self.game_over_message = None
        self.use_unicode = not self.args.ascii
        self.unicode_pieces = "♟♜♞♝♛♚♙♖♘♗♕♔"
        self.ascii_pieces = "PRNBQKprnbqk"
        if self.args.light:
            self.ascii_pieces = "prnbqkPRNBQK"
        self.unicode_replacements = dict(zip(self.ascii_pieces, self.unicode_pieces))
        assert self.height == len(self.display(None))
        assert self.width == len(self.display(None)[0])

    def get_cursor(self) -> tuple[int, int]:
        """get the position of the cursor in terms of display row and column"""
        if self.args.flip:
            real_row = (7 - self.cursor_row) * 4 + 7
        else:
            real_row = self.cursor_row * 4 + 7
        real_col = self.cursor_col * 6 + 4
        return real_row, real_col

    def flip_cursor_y(self, ch: int, key_up: int, key_down: int) -> int:
        """flip the key press for up and down"""
        if self.args.flip:
            if ch == key_up:
                return key_down
            elif ch == key_down:
                return key_up
        return ch

    def display(self, my_colour) -> list:
        board = self.board.display()
        if self.game_over_message is not None:
            header = f"Game over: {self.game_over_message}".center(self.width, " ")
        else:
            header = f"Current turn: {self.current_turn}{' (your turn)' if my_colour == self.current_turn else ''}".center(self.width, " ")
        board.insert(0, list(header))
        if self.selected_piece is None:
            header_2 = "Selected: None".center(self.width, " ")
        else:
            header_2 = f"Selected: {self.selected_piece}{self.col_labels[self.selected_piece.col]}{self.selected_piece.row + 1}".center(self.width, " ")
        board.insert(1, list(header_2))
        header_3 = f"Recent moves: {' | '.join(self.recent_moves_str[-3:])}".center(self.width, " ")
        board.insert(2, list(header_3))
        if self.use_unicode:
            white_queue = f"White: {' '.join([self.unicode_replacements[piece] for piece in self.white_birth_queue])}".center(self.width, " ")
            black_queue = f"Black: {' '.join([self.unicode_replacements[piece.lower()] for piece in self.black_birth_queue])}".center(self.width, " ")
        else:
            white_queue = f"White: {' '.join(self.white_birth_queue)}".center(self.width, " ")
            black_queue = f"Black: {' '.join(self.black_birth_queue)}".center(self.width, " ")
        if self.args.flip:
            board.insert(3, list(black_queue))
            board.append(list(white_queue))
        else:
            board.insert(3, list(white_queue))
            board.append(list(black_queue))
        return board

    def move_is_valid(self, source, dest, stockfish) -> bool:
        source_row = source.row + 1
        source_col = self.col_labels[source.col]
        dest_row = dest.row + 1
        dest_col = self.col_labels[dest.col]
        move_str = f"{source}{source_col}{source_row}->{dest}{dest_col}{dest_row}"
        if source.move_is_valid(dest):
            if stockfish is not None:
                try:
                    stockfish.set_fen_position(self.board.get_fen_position(self.current_turn))
                    if stockfish.is_move_correct(f"{source_col}{source_row}{dest_col}{dest_row}"):
                        self.recent_moves.append((source, dest))
                        self.recent_moves_str.append(move_str)
                        return True
                    else:
                        return False
                except:
                    self.recent_moves.append((source, dest))
                    self.recent_moves_str.append(move_str)
                    return True
            else:
                self.recent_moves.append((source, dest))
                self.recent_moves_str.append(move_str)
                return True
        else:
            return False

    def update_state(self, key, stockfish) -> bool:
        """returns whether there was a state change"""
        # cursor
        if key:
            if key == "up":
                self.cursor_row = (self.cursor_row - 1) % 8
            elif key == "down":
                self.cursor_row = (self.cursor_row + 1) % 8
            elif key == "left":
                self.cursor_col = (self.cursor_col - 1) % 8
            elif key == "right":
                self.cursor_col = (self.cursor_col + 1) % 8
            elif key == "stockfish":
                try:
                    if stockfish is not None:
                        stockfish.set_fen_position(self.board.get_fen_position(self.current_turn))
                        move = stockfish.get_best_move()
                        if move is not None:
                            self.selected_piece = self.board.get_piece(int(move[1]) - 1, ord(move[0]) - ord("a"))
                            self.cursor_row = int(move[3]) - 1
                            self.cursor_col = ord(move[2]) - ord("a")
                            key = "enter"
                except:
                    pass
            if key == "enter":
                if self.selected_piece is None:
                    # select a piece
                    if self.board.get_piece(self.cursor_row, self.cursor_col).side == self.current_turn:
                        self.selected_piece = self.board.get_piece(self.cursor_row, self.cursor_col)
                elif self.move_is_valid(self.selected_piece, self.board.get_piece(self.cursor_row, self.cursor_col), stockfish):
                    # move the selected piece to the cursor
                    try:
                        self.board.move_piece(self.selected_piece, self.board.get_piece(self.cursor_row, self.cursor_col))
                    except Exception as e:
                        self.game_over_message = str(e)
                        return False
                    self.selected_piece = None
                    self.current_turn = "black" if self.current_turn == "white" else "white"
                    # tick all the pieces at the start of the next turn
                    for piece in self.board.get_pieces():
                        piece.tick(self.board.get_surrounding_pieces(piece), self.current_turn, True)
                    # check if any pieces need to be born
                    if self.current_turn == "white":
                        for i in range(8):
                            for j in range(8):
                                piece = self.board.get_piece(i, j)
                                if piece.side == "empty":
                                    if piece.birth_counter_white == 3:
                                        next_piece = self.white_birth_queue.pop(0)
                                        self.board.set_new_piece(i, j, next_piece, "white")
                                        self.white_birth_queue.append(next_piece)
                    elif self.current_turn == "black":
                        for i in reversed(range(8)):
                            for j in range(8):
                                piece = self.board.get_piece(i, j)
                                if piece.side == "empty":
                                    if piece.birth_counter_black == 3:
                                        next_piece = self.black_birth_queue.pop(0)
                                        self.board.set_new_piece(i, j, next_piece, "black")
                                        self.black_birth_queue.append(next_piece)
                    # check if any pieces need to die
                    for piece in self.board.get_pieces():
                        if piece.death_counter == 4:
                            try:
                                self.board.kill_piece(piece, self.current_turn)
                            except Exception as e:
                                self.game_over_message = str(e)
                                return False
                    # recalculate nearby pieces for indicators
                    for piece in self.board.get_pieces():
                        piece.tick(self.board.get_surrounding_pieces(piece), self.current_turn, False)
                    return True
            if key == "other":
                self.selected_piece = None
        return False


class Board:
    def __init__(self, args) -> None:
        self.args = args
        self.board: list[list[Piece]] = [[Empty() for x in range(8)] for y in range(8)]
        self.board[0] = [
            Rook("white"),
            Knight("white"),
            Bishop("white"),
            Queen("white"),
            King("white"),
            Bishop("white"),
            Knight("white"),
            Rook("white")
        ]
        self.board[1] = [Pawn("white") for x in range(8)]
        self.board[6] = [Pawn("black") for x in range(8)]
        self.board[7] = [
            Rook("black"),
            Knight("black"),
            Bishop("black"),
            Queen("black"),
            King("black"),
            Bishop("black"),
            Knight("black"),
            Rook("black")
        ]
        self.piece_width = 5
        self.piece_height = 3
        for i in range(8):
            for j in range(8):
                self.board[i][j].set_position(i, j)

    def get_fen_position(self, current_turn: str) -> str:
        # return a string in Forsyth-Edwards Notation (FEN)
        fen = ""
        for row in reversed(self.board):
            empty_spaces = 0
            for piece in row:
                if piece.side == "empty":
                    empty_spaces += 1
                else:
                    if empty_spaces > 0:
                        fen += str(empty_spaces)
                        empty_spaces = 0
                    fen += str(piece)
            if empty_spaces > 0:
                fen += str(empty_spaces)
            fen += "/"
        fen = fen[:-1]
        fen += " " + current_turn[0] + " - - 0 1"
        return fen

    def get_piece(self, row: int, col: int) -> "Piece":
        return self.board[row][col]

    def get_pieces(self) -> list["Piece"]:
        pieces = []
        for row in self.board:
            for piece in row:
                pieces.append(piece)
        return pieces

    def get_surrounding_pieces(self, piece: "Piece") -> list["Piece"]:
        surrounding_pieces = []
        for i in range(-1, 2):
            for j in range(-1, 2):
                if i == 0 and j == 0:
                    continue
                elif 0 <= piece.row + i < 8 and 0 <= piece.col + j < 8:
                    surrounding_pieces.append(self.board[piece.row + i][piece.col + j])
        return surrounding_pieces

    def set_new_piece(self, row: int, col: int, piece: str, side: str) -> None:
        if piece == "P":
            self.board[row][col] = Pawn(side)
        elif piece == "R":
            self.board[row][col] = Rook(side)
        elif piece == "N":
            self.board[row][col] = Knight(side)
        elif piece == "B":
            self.board[row][col] = Bishop(side)
        elif piece == "Q":
            self.board[row][col] = Queen(side)
        elif piece == "K":
            self.board[row][col] = King(side)
        else:
            raise ValueError("invalid piece")
        self.board[row][col].set_position(row, col)

    def kill_piece(self, piece: "Piece", turn: str) -> None:
        """piece died due to over/under population"""
        if piece.side == turn:
            row = piece.row
            col = piece.col
            self.board[row][col].perish(conway=True)
            self.board[row][col] = Empty()
            self.board[row][col].set_position(row, col)

    def display(self) -> list:
        """get a version of the board suitable for printing to the ui"""
        # use ascii art to create a grid between the pieces
        WIDTH = self.piece_width
        HEIGHT = self.piece_height
        middle = ["─"] * WIDTH + ["┬"]
        board = [["┌"] + middle * 7 + ["─"] * WIDTH + ["┐"]]
        for row in reversed(self.board) if self.args.flip else self.board:
            board += [["│"], ["│"], ["│"]]
            for piece in row:
                board[-3] += piece.display()[0] + ["│"]
                board[-2] += piece.display()[1] + ["│"]
                board[-1] += piece.display()[2] + ["│"]
            middle = ["─"] * WIDTH + ["┼"]
            board += [["├"] + middle * 7 + ["─"] * WIDTH + ["┤"]]
        _ = board.pop()
        middle = ["─"] * WIDTH + ["┴"]
        board += [["└"] + middle * 7 + ["─"] * WIDTH + ["┘"]]
        # add the row and column numbers, NOTE: need to readjust if changing piece size
        for i in range(len(board)):
            if self.args.flip:
                board[i] = [str(9 - ((i + 2) // 4)) if (i + 2) % 4 == 0 else " "] + board[i] + [str(9 - ((i + 2) // 4)) if (i + 2) % 4 == 0 else " "]
            else:
                board[i] = [str((i + 2) // 4) if (i + 2) % 4 == 0 else " "] + board[i] + [str((i + 2) // 4) if (i + 2) % 4 == 0 else " "]
        board = [[" ", " ", " "] + list("     ".join(list("abcdefgh"))) + [" ", " ", " "]] + board
        board += [[" ", " ", " "] + list("     ".join(list("abcdefgh"))) + [" ", " ", " "]]
        return board

    def move_piece(self, source: "Piece", dest: "Piece") -> bool:
        """moves piece and returns whether move is successful"""
        if not source.move_is_valid(dest):
            return False
        # check if the move is a capture and move the piece
        self.board[dest.row][dest.col].perish(conway=False)
        self.board[dest.row][dest.col] = source
        # replace the old position with an empty piece
        self.board[source.row][source.col] = Empty()
        self.board[source.row][source.col].set_position(source.row, source.col)
        # update the position of the moved piece
        source.set_position(dest.row, dest.col)
        return True


class Piece:
    def __init__(self, side) -> None:
        """common attributes (required by every chess piece)"""
        self.side = side
        self.row = -1
        self.col = -1
        self.death_counter = 0
        self.birth_counter_white = 0
        self.birth_counter_black = 0
        self.surrounding_white = 0
        self.surrounding_black = 0

    def __str__(self) -> str:
        """for displaying entities on the map ui"""
        raise NotImplementedError()

    def set_position(self, row: int, col: int) -> None:
        """set the position of the piece"""
        self.row = row
        self.col = col

    def get_position(self) -> tuple[int, int]:
        """get the position of the piece"""
        return self.row, self.col

    def display(self) -> list[list[str]]:
        """get a 3x3 list of chars of the piece suitable for printing to the ui"""
        white_reproduction = "w" if self.side == "empty" and self.surrounding_white == 3 else " "
        black_reproduction = "l" if self.side == "empty" and self.surrounding_black == 3 else " "
        over_under_population = " "
        if self.side == "white":
            if self.surrounding_white < 2:
                over_under_population = "u"
            elif self.surrounding_white > 3:
                over_under_population = "o"
        elif self.side == "black":
            if self.surrounding_black < 2:
                over_under_population = "u"
            elif self.surrounding_black > 3:
                over_under_population = "o"
        chars_to_print = [
            [" " if white_reproduction == " " else str(self.birth_counter_white), " ", " ", " ", white_reproduction],
            [" " if over_under_population == " " else str(self.death_counter), " ", str(self), " ", over_under_population],
            [" " if black_reproduction == " " else str(self.birth_counter_black), " ", " ", " ", black_reproduction]
        ]
        return chars_to_print

    def move_is_valid(self, dest_piece: "Piece") -> bool:
        """check if the move is valid, TODO: check with chess logic, and add special moves"""
        if dest_piece.side == self.side:
            return False
        elif dest_piece.side == "empty":
            return True
        else:
            return True

    def tick(self, surrounding_pieces: list["Piece"], current_turn: str, update_counters: bool) -> None:
        """perform next step in life cycle, only ticks for players pieces before their turn"""
        self.surrounding_white = 0
        self.surrounding_black = 0
        for piece in surrounding_pieces:
            if piece.side == "white":
                self.surrounding_white += 1
            elif piece.side == "black":
                self.surrounding_black += 1
        if not update_counters:
            return
        if self.side == "empty":
            if current_turn == "white":
                if self.surrounding_white == 3:
                    self.birth_counter_white += 1
                else:
                    self.birth_counter_white = 0
            if current_turn == "black":
                if self.surrounding_black == 3:
                    self.birth_counter_black += 1
                else:
                    self.birth_counter_black = 0
        elif current_turn == self.side:
            same_pieces = 0
            for piece in surrounding_pieces:
                if piece.side == self.side:
                    same_pieces += 1
            if same_pieces < 2 or same_pieces > 3:
                self.death_counter += 1
            else:
                self.death_counter = 0

    def perish(self, conway: bool) -> None:
        """piece perished due to over/under population (conway=True) or capture (conway=False)"""
        pass

class Empty(Piece):
    def __init__(self) -> None:
        """empty space on the map"""
        super().__init__("empty")
    
    def __str__(self) -> str:
        return " "

    def move_is_valid(self, dest_piece: Piece) -> bool:
        return False

class Pawn(Piece):
    def __init__(self, side) -> None:
        """pawn chess piece"""
        super().__init__(side)
    
    def __str__(self) -> str:
        return "P" if self.side == "white" else "p"

class Knight(Piece):
    def __init__(self, side) -> None:
        """knight chess piece"""
        super().__init__(side)
    
    def __str__(self) -> str:
        return "N" if self.side == "white" else "n"

class Bishop(Piece):
    def __init__(self, side) -> None:
        """bishop chess piece"""
        super().__init__(side)
    
    def __str__(self) -> str:
        return "B" if self.side == "white" else "b"

class Rook(Piece):
    def __init__(self, side) -> None:
        """rook chess piece"""
        super().__init__(side)
    
    def __str__(self) -> str:
        return "R" if self.side == "white" else "r"

class Queen(Piece):
    def __init__(self, side) -> None:
        """queen chess piece"""
        super().__init__(side)
    
    def __str__(self) -> str:
        return "Q" if self.side == "white" else "q"

class King(Piece):
    def __init__(self, side) -> None:
        """king chess piece"""
        super().__init__(side)
    
    def __str__(self) -> str:
        return "K" if self.side == "white" else "k"

    def perish(self, conway) -> None:
        winning_side = "white" if self.side == "black" else "black"
        losing_side = "Black" if self.side == "black" else "White"
        if conway:
            raise Exception(f"{losing_side} king perished, {winning_side} wins!")
        else:
            raise Exception(f"{losing_side} king was captured, {winning_side} wins!")


if __name__ == "__main__":
    sys.exit(start_cli())
