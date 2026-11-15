# Lathe primer

A Lathe program is a list of definitions, one item per line. Definitions may
come in any order. Inside a unit, nothing may be used before the line that gives
it a value. Comments start with `--`. Indentation is free.

## Units

```
unit NAME
  why "plain words: what this unit is for"
  takes NAME : TYPE            -- one line per input, zero or more
  gives TYPE                   -- or: gives TYPE or fault
  needs EFFECT, EFFECT         -- only if it uses effects
  shrinks INPUT                -- only if the unit calls itself
  require CONDITION            -- must hold on entry
  ensure CONDITION             -- must hold for `result`
  example INPUTS gives VALUE   -- literals only; the checker runs it
  LABEL: STEP
end NAME
```

Header lines must come in exactly this order. `why` and `gives` are required.
Every step line starts with a label (`s0:`, `s1:`, ...).

## Steps

<!-- flat -->
Each step applies ONE operation to plain values. A plain value is a name, a
literal (`42`, `-1`, `"text"`, `true`, `false`, `nothing`, `[a, b, 3]`), a field
(`p.x`), or a constant (`maybe.none`, `map.empty`, a choice case with no value).
There are no parentheses and no nesting: give every intermediate result a name.

```
s0: total = a + b                     -- + - * / %  == != < <= > >=  and or
s1: flipped = not done
s2: n = seq.length xs                 -- operation name, then inputs separated by spaces
s3: r = my_unit a b                   -- call a unit
s4: big = when n > 10 then a else b   -- condition: VALUE or VALUE COMPARISON VALUE
s5: check total >= 0                  -- stops the program if false
s6: do console.write "hi"             -- run something that gives nothing
s7: give 0 when n == 0                -- early exit
s8: give big                          -- the last step is always an unconditional give
```

`give` takes one plain value. The most common mistake is putting two operations
in one step. Split them:

```
WRONG  s0: give n * n
RIGHT  s0: sq = n * n
       s1: give sq

WRONG  s0: x = when n % 2 == 0 then a else b
RIGHT  s0: r = n % 2
       s1: x = when r == 0 then a else b

WRONG  s0: ok = a > 0 and b > 0 and c > 0
RIGHT  s0: pa = a > 0
       s1: pb = b > 0
       s2: pc = c > 0
       s3: two = pa and pb
       s4: ok = two and pc

WRONG  s0: x = when a then 1 else when b then 2 else 3     -- no nested `when`
RIGHT  s0: inner = when b then 2 else 3
       s1: x = when a then 1 else inner
```
<!-- /flat -->
<!-- nested -->
A step gives a name to the value of an expression. Expressions are built from
literals (`42`, `-1`, `"text"`, `true`, `false`, `nothing`, `[a, b, 3]`), names,
fields (`p.x`), constants (`maybe.none`, `map.empty`), operators and calls.
A call is the operation name followed by its inputs separated by spaces. An
input that is itself a call or uses an operator goes in parentheses.

```
s0: total = a + b * 2                 -- + - * / %  == != < <= > >=  and or not
s1: n = seq.length xs                 -- call: name, then inputs separated by spaces
s2: d = int.abs (a - b)               -- parenthesise an input that is not a plain value
s3: m = seq.length (text.split t ",") -- nested call
s4: r = my_unit a (b + 1)             -- call a unit
s5: big = when n > 10 and d != 0 then a else b
s6: check total >= 0                  -- stops the program if false
s7: do console.write "hi"             -- run something that gives nothing
s8: give 0 when n == 0                -- early exit
s9: give big + 1                      -- the last step is always an unconditional give
```

Calls bind tighter than operators: `f a + 1` means `(f a) + 1`. Never write
`f(a, b)`; write `f a b`.
<!-- /nested -->

A name is given a value once and never changed. Equality is `==`. `/` rounds
down. Dividing by zero or overflowing 64 bits stops the program. Join text with
`text.concat a b` or `text.join [a, b, c] ""`, not `+`.

Reserved words, never usable as names: `record choice unit end give check do when
then else match on with new repeat limit fault or and not true false nothing`.

## Types

`int`, `bool`, `text`, `nothing`, `seq of T`, `map of K to V` (K is int, text or
bool), `maybe of T`, `pair of A and B` (read with `.first` and `.second`), and your
own records and choices. There is no character type: a character is a text of
length 1. An empty value must state its type:
`xs : seq of int = []` or `m : map of text to int = map.empty`.

## Records and choices

```
record point
  x : int
  y : int
end point

choice shape
  dot
  box : point
end shape
```

Build: `p = new point x = 1, y = 2` (fields in declared order), `s = shape.box p`,
`shape.dot`. Read a record with `p.x`. Read a choice or a maybe with `match`: one
line per case, in declared order, never nested inside anything else:

```
s3: area = match s
    on dot then 0
    on box b then b.x * b.y
s4: v = match found
    on some x then x + 1
    on none then 0
```

## Repetition

There are no loop statements. Use whole-collection operations. After `with`,
name a unit (or a built-in operation). It is handed each item, followed by any
extra inputs you list:

```
s0: doubled = seq.each xs with double
s1: large = seq.keep xs with above 10       -- calls `above ITEM 10`
s2: total = seq.fold xs 0 with add          -- calls `add SO_FAR ITEM`
s3: lower = seq.each words with text.lower
```

To carry several values through a fold or a repeat, make a record for them.

A unit may call itself if its header says `shrinks INPUT`: that input (an int
going down toward 0, or a text or seq getting shorter) must be smaller in every
self-call.

For repetition with no shrinking input, write a unit that takes a state and
gives `loop of STATE and RESULT`, built with `loop.again NEW_STATE` or
`loop.done RESULT`, and run it with `repeat START with UNIT limit COUNT`.
Running out of rounds is fault "limit", so `repeat` always needs an `else`.

## Faults

An operation marked `or fault` can fail, and the step must say what then:

```
s0: n = int.parse t else give fault    -- pass it to the caller; header needs `gives T or fault`
s1: n = int.parse t else 0             -- or use a fallback value
s2: give fault "empty" when len == 0   -- raise your own
```

## Effects

Effects are `console write`, `console read`, `files read`, `files write`, `clock`,
`random`. A unit lists in `needs` exactly the effects it, or anything it calls,
uses. A program starts at `unit main`, which takes nothing or
`takes args : seq of text`.

## A complete program

```
unit is_long
  why "is a word longer than a given length"
  takes w : text
  takes min : int
  gives bool
  s0: n = text.length w
  s1: yes = n > min
  s2: give yes
end is_long

unit long_words
  why "the words longer than min, upper-cased and sorted"
  takes line : text
  takes min : int
  gives seq of text
  example "a bbb cc dddd" 2 gives ["BBB", "DDDD"]
  s0: words = text.split_words line
  s1: long = seq.keep words with is_long min
  s2: upper = seq.each long with text.upper
  s3: sorted = seq.sort upper
  s4: give sorted
end long_words

unit count_down
  why "the numbers from n down to 1, by calling itself"
  takes n : int
  gives seq of int
  shrinks n
  example 3 gives [3, 2, 1]
  s0: give [] when n <= 0
  s1: less = n - 1
  s2: rest = count_down less
  s3: out = seq.concat [n] rest
  s4: give out
end count_down

record progress
  n : int
  rounds : int
end progress

unit halve_once
  why "one round of halving: stop at 1, otherwise halve and count the round"
  takes p : progress
  gives loop of progress and int
  s0: half = p.n / 2
  s1: more = p.rounds + 1
  s2: next = new progress n = half, rounds = more
  s3: out = when p.n <= 1 then loop.done p.rounds else loop.again next
  s4: give out
end halve_once

unit halvings
  why "how many times n can be halved before it reaches 1"
  takes n : int
  gives int
  example 8 gives 3
  s0: start = new progress n = n, rounds = 0
  s1: count = repeat start with halve_once limit 64 else -1
  s2: give count
end halvings
```
