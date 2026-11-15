"""Benchmark tasks: pure functions with a plain-words spec and a Python reference.

Each task is posed identically for every language; only the signature line differs.
Expected outputs come from running `ref` on `inputs`. A reference that raises
ValueError means the task expects a fault (Lathe) / ValueError (Python).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

PY_TYPES = {"int": "int", "bool": "bool", "text": "str"}


def py_type(t: str) -> str:
    if t in PY_TYPES:
        return PY_TYPES[t]
    if t.startswith("seq of "):
        return f"list[{py_type(t[7:])}]"
    if t.startswith("map of "):
        k, v = t[7:].split(" to ", 1)
        return f"dict[{py_type(k)}, {py_type(v)}]"
    raise ValueError(t)


@dataclass
class Task:
    name: str
    spec: str
    params: list            # [(name, lathe type)]
    ret: str                # lathe type
    ref: Callable
    inputs: list            # list of argument lists
    fallible: bool = False

    def tests(self) -> list:
        out = []
        for args in self.inputs:
            try:
                out.append((args, self.ref(*args), False))
            except ValueError:
                out.append((args, None, True))
        return out

    def lathe_signature(self) -> str:
        lines = [f"unit {self.name}"] + [f"  takes {n} : {t}" for n, t in self.params]
        lines.append(f"  gives {self.ret}" + (" or fault" if self.fallible else ""))
        return "\n".join(lines)

    def python_signature(self) -> str:
        args = ", ".join(f"{n}: {py_type(t)}" for n, t in self.params)
        return f"def {self.name}({args}) -> {py_type(self.ret)}:"


TASKS: list = []


def task(spec, params, ret, inputs, fallible=False):
    def register(fn):
        TASKS.append(Task(fn.__name__, " ".join(spec.split()), params, ret, fn, inputs, fallible))
        return fn
    return register


@task("Give the sum of the squares of the numbers.", [("xs", "seq of int")], "int",
      [[[1, 2, 3]], [[]], [[-4]], [[10, 10, 10, 10]]])
def sum_of_squares(xs):
    return sum(x * x for x in xs)


@task("Count the vowels (a, e, i, o, u, in either case) in the text.", [("t", "text")], "int",
      [["hello"], [""], ["AEIOU xyz"], ["rhythm"], ["Programming Language"]])
def count_vowels(t):
    return sum(1 for c in t.lower() if c in "aeiou")


@task("Is the text the same forwards and backwards, ignoring upper/lower case? The empty text counts as yes.",
      [("t", "text")], "bool", [["Level"], ["abc"], [""], ["a"], ["Abba"], ["ab"]])
def is_palindrome(t):
    return t.lower() == t.lower()[::-1]


@task("""For each number from 1 to n in order give "Fizz" if it is divisible by 3, "Buzz" if divisible by 5,
      "FizzBuzz" if divisible by both, and otherwise the number as decimal text. n is at least 0.""",
      [("n", "int")], "seq of text", [[0], [1], [5], [15], [16]])
def fizzbuzz(n):
    return ["FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i)
            for i in range(1, n + 1)]


@task("Give the greatest common divisor of two non-negative numbers. gcd(0, 0) is 0.",
      [("a", "int"), ("b", "int")], "int", [[12, 18], [0, 5], [7, 0], [0, 0], [17, 5], [1071, 462]])
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a


@task("Is n a prime number? Numbers below 2 are not prime.", [("n", "int")], "bool",
      [[0], [1], [2], [9], [97], [100], [7919], [-7]])
def is_prime(n):
    return n >= 2 and all(n % d for d in range(2, int(n ** 0.5) + 1))


@task("Give all prime numbers from 2 up to and including n, in increasing order.", [("n", "int")], "seq of int",
      [[1], [2], [10], [30], [50]])
def primes_up_to(n):
    return [k for k in range(2, n + 1) if all(k % d for d in range(2, int(k ** 0.5) + 1))]


@task("Give the n-th Fibonacci number, where fibonacci(0) is 0 and fibonacci(1) is 1. n is from 0 to 80.",
      [("n", "int")], "int", [[0], [1], [2], [10], [50], [80]])
def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


@task("Give the words of the text in opposite order, separated by single spaces. Words are separated by whitespace.",
      [("t", "text")], "text", [["one two three"], [""], ["  spaced   out  "], ["solo"]])
def reverse_words(t):
    return " ".join(reversed(t.split()))


@task("""Give the word that occurs most often in the text, comparing words in lower case and giving it in lower case.
      Words are separated by whitespace. If several words tie, give the one that comes first alphabetically.
      For a text with no words give the empty text.""",
      [("t", "text")], "text", [["the cat and The hat"], [""], ["b a"], ["x y x y z"], ["Zed zed apple"]])
def most_common_word(t):
    words = t.lower().split()
    if not words:
        return ""
    return min(set(words), key=lambda w: (-words.count(w), w))


@task("""Run-length encode the text: each run of the same character becomes that character followed by the
      length of the run in decimal. For example "aaabcc" becomes "a3b1c2". The empty text stays empty.""",
      [("t", "text")], "text", [["aaabcc"], [""], ["z"], ["aabbaa"], ["xxxxxxxxxxxx"]])
def run_length_encode(t):
    out, i = [], 0
    while i < len(t):
        j = i
        while j < len(t) and t[j] == t[i]:
            j += 1
        out.append(t[i] + str(j - i))
        i = j
    return "".join(out)


@task("""Shift every lower-case letter a-z forward by k places in the alphabet, wrapping from z back to a.
      Leave every other character unchanged. k may be any integer, including negative or larger than 26.""",
      [("t", "text"), ("k", "int")], "text", [["abc", 1], ["xyz", 3], ["Hello, world", 13], ["abc", -1], ["a", 53], ["", 4]])
def caesar_shift(t, k):
    return "".join(chr((ord(c) - 97 + k) % 26 + 97) if "a" <= c <= "z" else c for c in t)


@task("""Find two different positions i < j (counting from 0) whose numbers add up to target, and give [i, j].
      If several pairs work give the one with the smallest i, and for that i the smallest j. If none, give [].""",
      [("xs", "seq of int"), ("target", "int")], "seq of int",
      [[[2, 7, 11, 15], 9], [[1, 2, 3], 7], [[3, 3], 6], [[], 0], [[1, 4, 2, 3], 5], [[5], 10]])
def two_sum(xs, target):
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            if xs[i] + xs[j] == target:
                return [i, j]
    return []


@task("""xs is sorted in increasing order with no repeats. Give the position (counting from 0) of x in xs,
      or -1 if it is not there.""",
      [("xs", "seq of int"), ("x", "int")], "int",
      [[[1, 3, 5, 7, 9], 7], [[1, 3, 5, 7, 9], 4], [[], 1], [[2], 2], [[1, 2, 3, 4], 1], [[1, 2, 3, 4], 4]])
def find_sorted(xs, x):
    return xs.index(x) if x in xs else -1


@task("How many different values are in xs?", [("xs", "seq of int")], "int",
      [[[1, 2, 2, 3, 3, 3]], [[]], [[5, 5, 5]], [[-1, 1]]])
def count_distinct(xs):
    return len(set(xs))


@task("""Group words that are anagrams of each other (same letters, same counts). Give each group sorted
      alphabetically, and the groups ordered by their first word alphabetically.""",
      [("words", "seq of text")], "seq of seq of text",
      [[["eat", "tea", "tan", "ate", "nat", "bat"]], [[]], [["a"]], [["ab", "ba", "ab"]], [["z", "y", "x"]]])
def anagram_groups(words):
    groups = {}
    for w in words:
        groups.setdefault("".join(sorted(w)), []).append(w)
    return sorted(sorted(g) for g in groups.values())


@task("""Is every bracket in the text properly opened, closed and nested? Brackets are ( ) [ ] { }.
      Ignore all other characters. The empty text counts as yes.""",
      [("t", "text")], "bool", [["()"], ["([]{})"], ["(]"], ["(("], ["a(b)c]"], [""], ["{[()()]}"], [")("]])
def balanced_brackets(t):
    stack, pairs = [], {")": "(", "]": "[", "}": "{"}
    for c in t:
        if c in "([{":
            stack.append(c)
        elif c in pairs:
            if not stack or stack.pop() != pairs[c]:
                return False
    return not stack


@task("""Starting from n (at least 1): if the number is even halve it, otherwise triple it and add 1.
      How many such steps until the number is 1?""",
      [("n", "int")], "int", [[1], [2], [6], [27], [97]])
def collatz_steps(n):
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


@task("Give the sum of the decimal digits of n. n is at least 0.", [("n", "int")], "int",
      [[0], [7], [12345], [999999999], [1000000]])
def digit_sum(n):
    return sum(int(c) for c in str(n))


@task("Write n in binary as text, with no leading zeros (0 is \"0\"). n is at least 0.", [("n", "int")], "text",
      [[0], [1], [2], [10], [255], [1024]])
def to_binary(n):
    return bin(n)[2:]


@task("""The text holds whole numbers separated by commas, each possibly surrounded by spaces, for example
      "1, 20,-3". Give their sum. If any piece is not a whole number (including an empty piece), fail.""",
      [("t", "text")], "int", [["1, 20,-3"], ["42"], ["1,,2"], ["1, x"], [" 7 , 8 "], [""]], fallible=True)
def sum_csv(t):
    return sum(int(p.strip()) if p.strip().lstrip("-").isdigit() and p.strip().count("-") <= 1
               and (p.strip()[0] == "-" or "-" not in p.strip()) else _fail() for p in t.split(","))


def _fail():
    raise ValueError


@task("""Transpose a rectangular grid given as a sequence of equal-length rows: row r, column c of the input
      becomes row c, column r of the output. An empty grid gives an empty grid.""",
      [("m", "seq of seq of int")], "seq of seq of int",
      [[[[1, 2, 3], [4, 5, 6]]], [[]], [[[7]]], [[[1, 2], [3, 4], [5, 6]]]])
def transpose(m):
    return [list(r) for r in zip(*m)]


@task("""Give the longest text that every word starts with. For an empty sequence give the empty text.""",
      [("words", "seq of text")], "text",
      [[["flower", "flow", "flight"]], [["dog", "car"]], [[]], [["same", "same"]], [["prefix"]], [["ab", "a"]]])
def longest_common_prefix(words):
    if not words:
        return ""
    p = words[0]
    for w in words:
        while not w.startswith(p):
            p = p[:-1]
    return p


@task("""Give the second largest of the different values in xs. If xs has fewer than two different values, fail.""",
      [("xs", "seq of int")], "int", [[[1, 2, 3]], [[5, 5, 4]], [[7]], [[]], [[2, 2]], [[-1, -2, -3]]], fallible=True)
def second_largest(xs):
    vals = sorted(set(xs))
    if len(vals) < 2:
        raise ValueError
    return vals[-2]


@task("""Give a map from word length to how many of the words have that length.""",
      [("words", "seq of text")], "map of int to int",
      [[["a", "bb", "cc", "ddd"]], [[]], [["same", "size", "four"]], [["", "x"]]])
def count_by_length(words):
    out = {}
    for w in words:
        out[len(w)] = out.get(len(w), 0) + 1
    return out


@task("""Give the running totals: item i of the result is the sum of the first i+1 numbers of xs.""",
      [("xs", "seq of int")], "seq of int", [[[1, 2, 3, 4]], [[]], [[5]], [[-1, 1, -1, 1]]])
def running_total(xs):
    out, t = [], 0
    for x in xs:
        t += x
        out.append(t)
    return out


@task("""Write n as a Roman numeral using I V X L C D M with the usual subtractive forms
      (IV, IX, XL, XC, CD, CM). n is from 1 to 3999.""",
      [("n", "int")], "text", [[1], [4], [9], [14], [40], [1994], [3999], [2024]])
def roman_numeral(n):
    out = ""
    for v, s in [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                 (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
        while n >= v:
            out += s
            n -= v
    return out


@task("""Give the largest sum of any run of one or more neighbouring numbers in xs. For an empty xs give 0.""",
      [("xs", "seq of int")], "int",
      [[[-2, 1, -3, 4, -1, 2, 1, -5, 4]], [[]], [[-3, -1, -2]], [[5]], [[1, 2, 3]], [[2, -1, 2, -1, 2]]])
def max_run_sum(xs):
    if not xs:
        return 0
    best = cur = xs[0]
    for x in xs[1:]:
        cur = max(x, cur + x)
        best = max(best, cur)
    return best


@task("""Rotate xs to the left by k places: the first k items move to the end, in order. k is at least 0 and
      may be larger than the length of xs.""",
      [("xs", "seq of int"), ("k", "int")], "seq of int",
      [[[1, 2, 3, 4, 5], 2], [[1, 2, 3], 0], [[1, 2, 3], 4], [[], 3], [[9], 100]])
def rotate_left(xs, k):
    if not xs:
        return []
    k %= len(xs)
    return xs[k:] + xs[:k]


@task("""Evaluate a calculation in reverse Polish notation. Each token is a whole number or one of + - *.
      A number is pushed on a stack; an operator pops the top two values (the first popped is the right-hand
      side), applies itself and pushes the result. Give the single value left at the end. Fail if a token is
      neither a number nor an operator, if an operator finds fewer than two values, or if the stack does not
      hold exactly one value at the end.""",
      [("tokens", "seq of text")], "int",
      [[["2", "3", "+"]], [["5", "1", "2", "+", "4", "*", "+", "3", "-"]], [["4"]], [["1", "+"]], [["1", "2"]],
       [["2", "x", "+"]], [[]], [["10", "3", "-"]], [["-4", "2", "*"]]], fallible=True)
def eval_rpn(tokens):
    stack = []
    for tok in tokens:
        if tok in ("+", "-", "*"):
            if len(stack) < 2:
                raise ValueError
            b, a = stack.pop(), stack.pop()
            stack.append(a + b if tok == "+" else a - b if tok == "-" else a * b)
        elif tok.lstrip("-").isdigit() and tok.count("-") <= 1 and (tok[0] == "-" or "-" not in tok):
            stack.append(int(tok))
        else:
            raise ValueError
    if len(stack) != 1:
        raise ValueError
    return stack[0]


@task("""A walker starts at x = 0, y = 0. Each character of moves is a step: U adds 1 to y, D subtracts 1 from y,
      R adds 1 to x, L subtracts 1 from x; any other character is ignored. Give the final [x, y].""",
      [("moves", "text")], "seq of int", [["UURD"], [""], ["LLLL"], ["U?D!x"], ["RULD"], ["RRU"]])
def final_position(moves):
    x = y = 0
    for c in moves:
        x += (c == "R") - (c == "L")
        y += (c == "U") - (c == "D")
    return [x, y]


@task("""Give the items of xs that appear more than once, each listed one time, in increasing order.""",
      [("xs", "seq of int")], "seq of int", [[[1, 2, 2, 3, 3, 3]], [[]], [[4, 5, 6]], [[9, 1, 9, 1]]])
def duplicates(xs):
    return sorted({x for x in xs if xs.count(x) > 1})
