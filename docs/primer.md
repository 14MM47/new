# Lathe primer

A Lathe program is a list of definitions, one item per line. Nothing may be used
before the line that defines it. Comments start with `--`. Indentation is free.

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

## Steps

Each step applies ONE operation to plain values. A plain value is a name, a
literal (`42`, `-1`, `"text"`, `true`, `false`, `nothing`, `[a, b, 3]`), a field
(`p.x`), or a constant (`maybe.none`, `map.empty`, a choice case with no value).
There are no parentheses and no nesting: give every intermediate result a name.

```
s0: total = a + b                     -- + - * / %  == != < <= > >=  and or
s1: flipped = not done
s2: n = seq.length xs                 -- operation name, then inputs separated by spaces
s3: r = my_unit a b                   -- call a unit defined above
s4: big = when n > 10 then a else b   -- condition: VALUE or VALUE COMPARISON VALUE
s5: check total >= 0                  -- stops the program if false
s6: do console.write "hi"             -- run something that gives nothing
s7: give 0 when n == 0                -- early exit
s8: give big                          -- the last step is always an unconditional give
```

`give` takes one plain value. A name is given a value once and never changed.
Equality is `==`. `/` rounds down. Dividing by zero or overflowing 64 bits stops the program.
Join text with `text.concat a b` or `text.join [a, b, c] ""`, not `+`.

## Types

`int`, `bool`, `text`, `nothing`, `seq of T`, `map of K to V` (K is int, text or
bool), `maybe of T`, `pair of A and B` (read with `.first` and `.second`), and your
own records and choices. An empty value must state its type:
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
line per case, in declared order, each holding one operation:

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
name a unit defined above (or a built-in operation). It is handed each item,
followed by any extra inputs you list:

```
s0: doubled = seq.each xs with double
s1: large = seq.keep xs with above 10       -- calls `above ITEM 10`
s2: total = seq.fold xs 0 with add          -- calls `add SO_FAR ITEM`
s3: lower = seq.each words with text.lower
```

For open-ended repetition, write a unit that takes a state and gives
`loop of STATE and RESULT`, built with `loop.again NEW_STATE` or `loop.done RESULT`,
and run it with a stated limit. Running out of rounds is fault "limit":

```
s0: r = repeat start with halve limit 64 else give fault
```

A unit may also call itself if its header says `shrinks INPUT`: that input (an
int going down toward 0, or a text or seq getting shorter) must be smaller in
every self-call.

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

## A complete unit

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

unit longest_words
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
end longest_words
```
