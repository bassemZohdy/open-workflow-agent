from pathlib import Path

from google.adk.evaluation import AgentEvaluator


async def test_adk_evaluation_gate() -> None:
    await AgentEvaluator.evaluate(
        agent_module="tests.eval.eval_agent",
        eval_dataset_file_path_or_dir=str(
            Path(__file__).parents[2] / "tests" / "data" / "adk-evaluation.test.json"
        ),
        num_runs=1,
        print_detailed_results=False,
    )
