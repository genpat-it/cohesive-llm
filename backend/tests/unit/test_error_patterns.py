import unittest

from tests.error_patterns import parse_nextflow_output


class TestErrorPatternsUnit(unittest.TestCase):
    def test_parse_syntax_compilation_error(self):
        stderr = """
        ERROR ~ Script compilation error
        Unexpected input: '{' @ line 3, column 23.
        """
        parsed = parse_nextflow_output(stdout="", stderr=stderr)

        self.assertTrue(any(e["category"] == "script_compilation_error" for e in parsed["fatal_errors"]))
        self.assertTrue(any(e["category"] == "syntax_unexpected_input" for e in parsed["fatal_errors"]))

        # Verify captures
        syntax_err = next(e for e in parsed["fatal_errors"] if e["category"] == "syntax_unexpected_input")
        self.assertEqual(syntax_err["captures"]["token"], "{")
        self.assertEqual(syntax_err["captures"]["line_number"], "3")
        self.assertEqual(syntax_err["captures"]["column_number"], "23")

    def test_parse_undefined_variable(self):
        stderr = "No such variable: prefix"
        parsed = parse_nextflow_output(stdout="", stderr=stderr)

        self.assertTrue(any(e["category"] == "undefined_variable" for e in parsed["fatal_errors"]))
        err = next(e for e in parsed["fatal_errors"] if e["category"] == "undefined_variable")
        self.assertEqual(err["captures"]["variable_name"], "prefix")

    def test_parse_channel_count_mismatch(self):
        stderr = "Process `PROCESS_FILES` declares 1 input channel but 2 were specified"
        parsed = parse_nextflow_output(stdout="", stderr=stderr)

        self.assertTrue(any(e["category"] == "channel_count_mismatch" for e in parsed["fatal_errors"]))
        err = next(e for e in parsed["fatal_errors"] if e["category"] == "channel_count_mismatch")
        self.assertEqual(err["captures"]["process_name"], "PROCESS_FILES")
        self.assertEqual(err["captures"]["expected_count"], "1")
        self.assertEqual(err["captures"]["provided_count"], "2")

    def test_parse_process_nonzero_exit_and_command_not_found(self):
        stderr = """
        ERROR ~ Error executing process > 'PROCESS_FILES (3)'
        .command.sh: line 2: cowpy: command not found
        Process `PROCESS_FILES (3)` terminated with an error exit status (127)
        """
        parsed = parse_nextflow_output(stdout="", stderr=stderr)

        self.assertTrue(any(e["category"] == "process_execution_error" for e in parsed["fatal_errors"]))
        self.assertTrue(any(e["category"] == "process_command_not_found" for e in parsed["fatal_errors"]))
        self.assertTrue(any(e["category"] == "process_nonzero_exit" for e in parsed["fatal_errors"]))

        cmd_err = next(e for e in parsed["fatal_errors"] if e["category"] == "process_command_not_found")
        self.assertEqual(cmd_err["captures"]["command"], "cowpy")

        exit_err = next(e for e in parsed["fatal_errors"] if e["category"] == "process_nonzero_exit")
        self.assertEqual(exit_err["captures"]["process_name"], "PROCESS_FILES (3)")
        self.assertEqual(exit_err["captures"]["exit_code"], "127")

    def test_noise_warnings_ignored_as_fatal(self):
        stderr = """
        WARN: file not found: '/data/result/*.fastq'
        WARN: channel defined outside workflow block
        Missing required parameter: --genome
        """
        parsed = parse_nextflow_output(stdout="", stderr=stderr)

        # None of these should be fatal
        self.assertEqual(len(parsed["fatal_errors"]), 0)

        # All of these should be parsed as noise/warnings
        self.assertTrue(any(e["category"] == "warn_file_not_found" for e in parsed["noise_errors"]))
        self.assertTrue(any(e["category"] == "warn_channel_outside_workflow" for e in parsed["noise_errors"]))
        self.assertTrue(any(e["category"] == "missing_param" for e in parsed["noise_errors"]))

    def test_script_location_trailer_extraction(self):
        stderr = """
        ERROR ~ Script compilation error
        -- Check script 'workflow.nf' at line 20
        """
        parsed = parse_nextflow_output(stdout="", stderr=stderr)
        self.assertIsNotNone(parsed["script_location"])
        self.assertEqual(parsed["script_location"]["file"], "workflow.nf")
        self.assertEqual(parsed["script_location"]["line"], 20)
