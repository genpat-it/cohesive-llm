# Evaluation schemas, judge prompts, and runner functions.
#
# Clean imports:
#   from tests.evaluation.schemas import AcademicEval, ArchitectEval, DiagramEval
#   from tests.evaluation.prompts import CONSULTANT_TEST_PROMPT, JUDGE_PROMPT, ...
#   from tests.evaluation.judge   import judge_consultant, judge_pipeline, ...

from tests.evaluation.schemas import (
    AcademicEval,
    ArchitectEval,
    DiagramEval,
    RejectionEval,
    CodeRecreationEval,
)

from tests.evaluation.prompts import (
    CONSULTANT_TEST_PROMPT,
    JUDGE_PROMPT,
    PIPELINE_JUDGE_PROMPT,
    DIAGRAM_JUDGE_PROMPT,
    REJECTION_JUDGE_PROMPT,
    CODE_RECREATION_JUDGE_PROMPT,
)

