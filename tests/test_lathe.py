import io
import os
import tempfile
import textwrap
import unittest

from lathe import store
from lathe.builtins import Fault, Trap
from lathe.checker import Session, check_source
from lathe.cli import answer, run_program
from lathe.interp import Grant, Interp, Runtime, fmt


def check(src: str) -> Session:
    return check_source(textwrap.dedent(src))


def codes(src: str) -> list:
    return [v.code for v in check(src).verdicts]


def call(src: str, unit: str, *args):
    s = check(src)
    assert not s.verdicts, "\n".join(v.render() for v in s.verdicts)
    return Interp(s.prog, Runtime.sealed(), budget=1_000_000).call(unit, list(args))


def unit(body: str, header: str = "takes n : int\n  gives int") -> str:
    """Wrap steps in a unit `f`."""
    return f'unit f\n  why "test"\n  {header}\n' + textwrap.indent(textwrap.dedent(body).strip(), "  ") + "\nend f\n"


ABS = """
unit abs
  why "distance from zero"
  takes n : int
  gives int
  ensure result >= 0
  example 3 gives 3
  example -4 gives 4
  s0: m = 0 - n
  s1: r = when n < 0 then m else n
  s2: give r
end abs
"""


class Accepts(unittest.TestCase):
    def test_simple_unit_and_examples(self):
        s = check(ABS)
        self.assertEqual(s.verdicts, [])
        self.assertIn("2 example(s) pass", s.prog.units["abs"].evidence)
        self.assertEqual(call(ABS, "abs", -9), 9)

    def test_arithmetic_and_floor_division(self):
        src = unit("s0: q = n / 2\ns1: r = n % 2\ns2: t = q * 10\ns3: out = t + r\ns4: give out")
        self.assertEqual(call(src, "f", 7), 31)
        self.assertEqual(call(src, "f", -7), -39)   # floor: -7 / 2 == -4, -7 % 2 == 1

    def test_records_and_choices(self):
        src = """
        record point
          x : int
          y : int
        end point
        choice shape
          dot
          box : point
        end shape
        unit area
          why "area of a shape"
          takes s : shape
          gives int
          s0: a = match s
              on dot then 0
              on box p then p.x * p.y
          s1: give a
        end area
        unit demo
          why "build and measure"
          takes w : int
          gives int
          example 3 gives 12
          s0: p = new point x = w, y = 4
          s1: b = shape.box p
          s2: a = area b
          s3: z = area shape.dot
          s4: out = a + z
          s5: give out
        end demo
        """
        self.assertEqual(call(src, "demo", 5), 20)

    def test_collections(self):
        src = """
        unit double
          why "twice"
          takes n : int
          gives int
          s0: d = n * 2
          s1: give d
        end double
        unit add
          why "sum step"
          takes acc : int
          takes n : int
          gives int
          s0: t = acc + n
          s1: give t
        end add
        unit bigger
          why "is n above a floor"
          takes n : int
          takes floor : int
          gives bool
          s0: b = n > floor
          s1: give b
        end bigger
        unit run
          why "pipeline"
          takes xs : seq of int
          gives int
          example [1, 2, 3, 4] gives 18
          example [] gives 0
          s0: ds = seq.each xs with double
          s1: big = seq.keep ds with bigger 3
          s2: total = seq.fold big 0 with add
          s3: give total
        end run
        """
        self.assertEqual(call(src, "run", [5]), 10)

    def test_with_builtin_and_maps(self):
        src = unit("""
            s0: ws = text.split_words t
            s1: low = seq.each ws with text.lower
            s2: counts = seq.tally low
            s3: n = map.get_or counts "the" 0
            s4: give n
        """, "takes t : text\n  gives int\n  example \"The the cat\" gives 2")
        self.assertEqual(call(src, "f", "a b"), 0)

    def test_maybe_and_pairs(self):
        src = unit("""
            s0: first = seq.first xs
            s1: v = match first
                on some x then x + 1
                on none then -1
            s2: p = pair.make v "k"
            s3: give p.first
        """, "takes xs : seq of int\n  gives int\n  example [41] gives 42\n  example [] gives -1")
        self.assertEqual(codes(src), [])

    def test_empty_literal_needs_annotation(self):
        self.assertEqual(codes(unit("s0: xs = []\ns1: give n")), ["E-AMBIG"])
        self.assertEqual(codes(unit("s0: xs : seq of int = []\ns1: m = seq.length xs\ns2: give m")), [])
        self.assertEqual(codes(unit("s0: m : map of text to int = map.empty\ns1: k = map.size m\ns2: give k")), [])

    def test_recursion_with_shrinks(self):
        src = """
        unit fact
          why "factorial"
          takes n : int
          gives int
          shrinks n
          require n >= 0
          example 5 gives 120
          s0: give 1 when n == 0
          s1: m = n - 1
          s2: rest = fact m
          s3: out = n * rest
          s4: give out
        end fact
        """
        self.assertEqual(call(src, "fact", 10), 3628800)
        with self.assertRaises(Trap) as cm:
            call(src, "fact", -1)
        self.assertEqual(cm.exception.kind, "require")

    def test_repeat(self):
        src = """
        unit halve
          why "one halving round, counting rounds"
          takes st : pair of int and int
          gives loop of pair of int and int and int
          s0: done = st.first <= 1
          s1: half = st.first / 2
          s2: rounds = st.second + 1
          s3: next = pair.make half rounds
          s4: out = when done then loop.done st.second else loop.again next
          s5: give out
        end halve
        unit log2
          why "how many halvings reach 1"
          takes n : int
          gives int or fault
          example 8 gives 3
          example 1 gives 0
          s0: start = pair.make n 0
          s1: r = repeat start with halve limit 64 else give fault
          s2: give r
        end log2
        """
        self.assertEqual(call(src, "log2", 1024), 10)

    def test_repeat_limit_faults(self):
        src = """
        unit spin
          why "never finishes"
          takes n : int
          gives loop of int and int
          s0: out : loop of int and int = loop.again n
          s1: give out
        end spin
        unit f
          why "bounded"
          takes n : int
          gives int
          example 1 gives -1
          s0: r = repeat n with spin limit 10 else -1
          s1: give r
        end f
        """
        self.assertEqual(codes(src), [])


class Faults(unittest.TestCase):
    def test_fault_must_be_handled(self):
        self.assertEqual(codes(unit("s0: v = int.parse t\ns1: give v", "takes t : text\n  gives int")), ["E-FAULT"])

    def test_fault_fallback_and_propagation(self):
        fallback = unit("s0: v = int.parse t else 0\ns1: give v",
                        "takes t : text\n  gives int\n  example \"12\" gives 12\n  example \"x\" gives 0")
        self.assertEqual(codes(fallback), [])
        passes = unit("s0: v = int.parse t else give fault\ns1: give v",
                      "takes t : text\n  gives int or fault\n  example \"x\" gives fault \"parse\"")
        self.assertEqual(codes(passes), [])
        with self.assertRaises(Fault):
            call(passes, "f", "nope")

    def test_give_fault_needs_fallible_unit(self):
        self.assertEqual(codes(unit('s0: give fault "bad" when n < 0\ns1: give n')), ["E-FAULT"])

    def test_else_without_fault(self):
        self.assertEqual(codes(unit("s0: m = n + 1 else 0\ns1: give m")), ["E-FAULT"])

    def test_fault_through_with(self):
        src = unit("s0: ns = seq.each ts with int.parse else give fault\ns1: t = seq.sum ns\ns2: give t",
                   'takes ts : seq of text\n  gives int or fault\n  example ["1", "2"] gives 3\n  example ["1", "z"] gives fault "parse"')
        self.assertEqual(codes(src), [])


class Rejects(unittest.TestCase):
    def test_unknown_name_suggests(self):
        v = check(unit("s0: m = seq.lenght xs\ns1: give m", "takes xs : seq of int\n  gives int")).verdicts[0]
        self.assertEqual(v.code, "E-NAME")
        self.assertIn("seq.length", v.fixes[0])
        self.assertEqual((v.definition, v.label), ("f", "s0"))

    def test_no_forward_reference(self):
        self.assertEqual(codes(unit("s0: m = later + 1\ns1: later = 2\ns2: give m")), ["E-NAME"])

    def test_one_operation_per_step(self):
        self.assertEqual(codes(unit("s0: m = n + 1 + 2\ns1: give m")), ["E-SYNTAX"])
        self.assertEqual(codes(unit("s0: m = int.abs n + 1\ns1: give m")), ["E-SYNTAX"])
        self.assertEqual(codes(unit("s0: give n + 1")), ["E-SYNTAX"])

    def test_type_errors(self):
        self.assertEqual(codes(unit('s0: m = n + "a"\ns1: give m')), ["E-TYPE"])
        self.assertEqual(codes(unit('s0: give "text"')), ["E-TYPE"])
        self.assertEqual(codes(unit("s0: m = when n then 1 else 2\ns1: give m")), ["E-TYPE"])
        v = check(unit('s0: m = t + t\ns1: give m', "takes t : text\n  gives text")).verdicts[0]
        self.assertIn("text.concat", v.fixes[0])

    def test_single_assignment(self):
        self.assertEqual(codes(unit("s0: m = n + 1\ns1: m = m + 1\ns2: give m")), ["E-DUP"])

    def test_arity(self):
        self.assertEqual(codes(unit("s0: m = int.min n\ns1: give m")), ["E-ARITY"])

    def test_missing_give_and_unreachable(self):
        self.assertEqual(codes(unit("s0: m = n + 1")), ["E-GIVE"])
        self.assertEqual(codes(unit("s0: give n\ns1: m = n + 1")), ["E-UNREACHABLE"])

    def test_effects_must_be_declared_and_used(self):
        body = 's0: do console.write "hi"\ns1: give n'
        self.assertEqual(codes(unit(body)), ["E-EFFECT"])
        self.assertEqual(codes(unit(body, "takes n : int\n  gives int\n  needs console write")), [])
        self.assertEqual(codes(unit("s0: give n", "takes n : int\n  gives int\n  needs console write")), ["E-EFFECT"])

    def test_effects_flow_through_calls(self):
        src = unit('s0: do console.write "hi"\ns1: give n', "takes n : int\n  gives int\n  needs console write") + """
unit g
  why "caller"
  takes n : int
  gives int
  s0: m = f n
  s1: give m
end g
"""
        self.assertEqual(codes(src), ["E-EFFECT"])

    def test_match_arms_in_order_and_complete(self):
        base = "takes xs : seq of int\n  gives int"
        self.assertEqual(codes(unit("s0: h = seq.first xs\ns1: v = match h\n  on none then 0\n  on some x then x\ns2: give v", base)), ["E-ARMS"])
        self.assertEqual(codes(unit("s0: h = seq.first xs\ns1: v = match h\n  on some x then x\ns2: give v", base)), ["E-ARMS"])
        self.assertEqual(codes(unit("s0: h = seq.first xs\ns1: v = match h\n  on some then 1\n  on none then 0\ns2: give v", base)), ["E-ARMS"])

    def test_header_order_and_why(self):
        self.assertEqual(codes("unit f\n  takes n : int\n  gives int\n  s0: give n\nend f"), ["E-ORDER"])
        self.assertEqual(codes('unit f\n  why "x"\n  gives int\n  takes n : int\n  s0: give 1\nend f'), ["E-ORDER"])

    def test_example_failure_reports_actual(self):
        v = check(unit("s0: m = n + 1\ns1: give m", "takes n : int\n  gives int\n  example 1 gives 3")).verdicts[0]
        self.assertEqual(v.code, "E-EXAMPLE")
        self.assertIn("gives 2, not 3", v.message)

    def test_recursion_requires_shrinks(self):
        self.assertEqual(codes(unit("s0: m = f n\ns1: give m")), ["E-SHRINKS"])

    def test_one_verdict_per_definition_and_blocked_dependants(self):
        src = unit("s0: m = nope\ns1: q = also_nope\ns2: give m") + """
unit g
  why "uses the broken unit"
  takes n : int
  gives int
  s0: m = f n
  s1: give m
end g
unit h
  why "independent"
  takes n : int
  gives int
  s0: give n
end h
"""
        s = check(src)
        self.assertEqual([v.code for v in s.verdicts], ["E-NAME", "E-BLOCKED"])
        self.assertEqual(list(s.prog.units), ["h"])

    def test_unclosed_and_wrong_end(self):
        self.assertEqual(codes('unit f\n  why "x"\n  gives int\n  s0: give 1'), ["E-END"])
        self.assertEqual(codes('unit f\n  why "x"\n  gives int\n  s0: give 1\nend g'), ["E-END"])

    def test_map_key_type(self):
        self.assertEqual(codes(unit("s0: m : map of seq of int to int = map.empty\ns1: give n")), ["E-TYPE"])

    def test_sort_needs_ordered_items(self):
        self.assertEqual(codes(unit("s0: ys = seq.sort xs\ns1: give n", "takes n : int\n  takes xs : seq of bool\n  gives int")), ["E-TYPE"])


class Traps(unittest.TestCase):
    def test_overflow_divide_check(self):
        with self.assertRaises(Trap) as cm:
            call(unit("s0: m = n * n\ns1: give m"), "f", 2**62)
        self.assertEqual(cm.exception.kind, "overflow")
        self.assertEqual(cm.exception.where, ("f", "s0"))
        with self.assertRaises(Trap) as cm:
            call(unit("s0: m = 1 / n\ns1: give m"), "f", 0)
        self.assertEqual(cm.exception.kind, "divide")
        with self.assertRaises(Trap) as cm:
            call(unit("s0: check n > 0\ns1: give n"), "f", 0)
        self.assertEqual(cm.exception.kind, "check")

    def test_ensure(self):
        with self.assertRaises(Trap) as cm:
            call(unit("s0: give n", "takes n : int\n  gives int\n  ensure result > 0"), "f", -1)
        self.assertEqual(cm.exception.kind, "ensure")

    def test_measure_must_shrink(self):
        src = unit("s0: give 0 when n == 0\ns1: m = f n\ns2: give m", "takes n : int\n  gives int\n  shrinks n")
        with self.assertRaises(Trap) as cm:
            call(src, "f", 3)
        self.assertEqual(cm.exception.kind, "measure")


MAIN = """
unit main
  why "copy a file's first line to the screen"
  takes args : seq of text
  gives nothing or fault
  needs console write, files read
  s0: path = seq.at args 0
  s1: p = maybe.or path ""
  s2: raw = files.read_text p else give fault
  s3: lines = text.split_lines raw
  s4: first = seq.first lines
  s5: line = maybe.or first ""
  s6: do console.write line
  s7: give nothing
end main
"""


class Running(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "in.txt")
        with open(self.path, "w") as fh:
            fh.write("hello\nworld\n")
        self.prog = check(MAIN).prog

    def run_main(self, grant, **kw):
        out = io.StringIO()
        code = run_program(self.prog, Grant.parse(grant), [self.path], stdout=out, **kw)
        return code, out.getvalue()

    def test_grant_allows(self):
        self.assertEqual(self.run_main(["console write", f"files read {self.dir}"]), (0, "hello\n"))

    def test_missing_grant_refuses_to_start(self):
        self.assertEqual(self.run_main(["console write"])[0], 2)

    def test_path_outside_grant_is_denied(self):
        other = tempfile.mkdtemp()
        self.assertEqual(self.run_main(["console write", f"files read {other}"]), (1, ""))

    def test_record_then_replay_without_the_file(self):
        trace = []
        self.assertEqual(self.run_main(["console write, files read"], record=trace)[0], 0)
        self.assertEqual([e["op"] for e in trace], ["files.read_text", "console.write"])
        os.remove(self.path)
        self.assertEqual(self.run_main(["console write, files read"], replay=trace), (0, "hello\n"))


class Dialogue(unittest.TestCase):
    def test_interactive_rejects_line_and_keeps_state(self):
        s = Session(interactive=True)
        for line in ["unit f", '  why "t"', "  takes n : int", "  gives int"]:
            self.assertEqual(s.feed(line), [])
        self.assertEqual(s.feed("  s0: m = n + bogus")[0].code, "E-NAME")
        self.assertEqual(s.feed("  s0: m = n + 1"), [])
        self.assertEqual(s.feed("  s1: give m"), [])
        self.assertEqual(s.expect(), ["end f"])
        self.assertEqual(s.feed("end f"), [])
        self.assertIn("f", s.prog.units)

    def test_undo(self):
        s = Session(interactive=True)
        for line in ["unit f", '  why "t"', "  gives int", "  s0: m = 1"]:
            s.feed(line)
        self.assertTrue(s.undo())
        self.assertEqual(s.feed("  s0: m = 2"), [])

    def test_expect_names_the_next_match_arm(self):
        s = Session(interactive=True)
        for line in ["unit f", '  why "t"', "  takes xs : seq of int", "  gives int", "  s0: h = seq.first xs", "  s1: v = match h"]:
            self.assertEqual(s.feed(line), [])
        self.assertEqual(s.expect(), ["on some NAME then ..."])
        s.feed("    on some x then x")
        self.assertEqual(s.expect(), ["on none then ..."])

    def test_ask(self):
        s = check(ABS)
        self.assertIn("seq.tally", answer(s, 'ask find "count how often each item occurs"'))
        self.assertIn("abs", answer(s, 'ask find "distance from zero"'))
        self.assertIn("ensure result >= 0", answer(s, "ask card abs"))
        self.assertIn("seq.fold", answer(s, "ask card seq.fold"))

    def test_hash_ignores_why_and_tracks_dependencies(self):
        a = check(ABS).prog.units["abs"].hash
        b = check(ABS.replace("distance from zero", "magnitude")).prog.units["abs"].hash
        c = check(ABS.replace("0 - n", "n * -1")).prog.units["abs"].hash
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class Formatting(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(fmt([1, "a", True, None]), '[1, "a", true, nothing]')
        self.assertEqual(fmt({"k": [("some", 1), ("none", None)]}), '{"k": [some 1, none]}')


if __name__ == "__main__":
    unittest.main()


class Variants(unittest.TestCase):
    def nested(self, src):
        return check_source(textwrap.dedent(src), nested=True)

    def test_definitions_may_come_in_any_order(self):
        src = """
        unit top
          why "uses a helper written below"
          takes n : int
          gives int
          example 2 gives 5
          s0: m = helper n
          s1: give m
        end top
        unit helper
          why "double and add one"
          takes n : int
          gives int
          s0: d = n * 2
          s1: out = d + 1
          s2: give out
        end helper
        """
        self.assertEqual(codes(src), [])

    def test_header_words_and_result_are_usable_names(self):
        self.assertEqual(codes(unit("s0: result = n + 1\ns1: gives = result * 2\ns2: give gives")), [])
        v = check(unit("s0: limit = n + 1\ns1: give limit")).verdicts[0]
        self.assertIn("reserved word", v.message)

    def test_flat_rejects_parentheses(self):
        self.assertEqual(codes(unit("s0: m = (n + 1) * 2\ns1: give m")), ["E-SYNTAX"])

    def test_nested_expressions(self):
        src = unit("""
            s0: m = (n + 1) * 2 - n % 3
            s1: big = when m > 10 and not (n == 0) then int.max m 100 else 0 - m
            s2: xs = [m, big, n * n]
            s3: give seq.length xs + seq.sum xs when n > 0
            s4: give when n == 0 then -1 else int.abs (n - 1)
        """, "takes n : int\n  gives int\n  example 5 gives 28\n  example 7 gives 167\n  example 0 gives -1\n  example -3 gives 4")
        s = self.nested(src)
        self.assertEqual([v.render() for v in s.verdicts], [])

    def test_nested_and_or_skip_the_right_side(self):
        src = unit("s0: give n != 0 and 10 / n > 1", "takes n : int\n  gives bool\n  example 0 gives false\n  example 5 gives true")
        self.assertEqual(self.nested(src).verdicts, [])

    def test_nested_faults_still_need_handling(self):
        base = "takes t : text\n  gives int or fault"
        self.assertEqual([v.code for v in self.nested(unit("s0: n = int.parse t + 1\ns1: give n", base)).verdicts], ["E-FAULT"])
        self.assertEqual(self.nested(unit("s0: n = int.parse t + 1 else give fault\ns1: give n", base)).verdicts, [])
        self.assertEqual([v.code for v in self.nested(unit("s0: give int.parse t", base)).verdicts], ["E-FAULT"])


class Primer(unittest.TestCase):
    def test_every_program_in_the_primer_checks(self):
        from lathe.cli import primer_text
        import re
        for nested in (False, True):
            text = primer_text(nested)
            blocks = [b for b in re.findall(r"```\n(.*?)```", text, re.S) if re.match(r"(record|choice|unit) [a-z_]+\n  ", b)]
            self.assertGreaterEqual(len(blocks), 2)
            for block in blocks:
                s = check_source(block, nested=nested)
                self.assertEqual([v.render() for v in s.verdicts], [], block)

    def test_variants_differ_only_where_marked(self):
        from lathe.cli import primer_text
        self.assertIn("no parentheses", primer_text(False))
        self.assertNotIn("no parentheses", primer_text(True))
        self.assertNotIn("<!--", primer_text(False) + primer_text(True))
