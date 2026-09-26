import argparse
from .agent import Agent


def main():
    parser = argparse.ArgumentParser(description="OpenShip Workflow PoC")
    parser.add_argument("workflow", help="Path to workflow markdown file")
    args = parser.parse_args()

    agent = Agent()
    workflow = agent.load_workflow(args.workflow)
    print(f"Loaded workflow: {workflow['title']}")
    print(f"Steps: {len(workflow['steps'])}")

    # Approval loop with regeneration
    while True:
        print("\nPlan:")
        for step in workflow["steps"]:
            print(f"  {step['order']}. {step['description']} ({step['tool']})")

        response = input("\nApprove plan? [yes/no]: ").lower().strip()
        if response == "yes" or response == "y":
            break
        elif response == "no" or response == "n":
            feedback = input("What would you like to change? ")
            # Regenerate plan based on feedback (future: LLM-based)
            print(f"Regenerating plan with feedback: {feedback}")
            # For now, just reload the workflow
            workflow = agent.load_workflow(args.workflow)
        else:
            print("Invalid response. Please enter 'yes' or 'no'.")

    print("\nExecuting workflow...")
    agent.compile_graph()
    result = agent.execute()
    print("\nResults:")
    for step_order, output in result["outputs"].items():
        print(f"  Step {step_order}: {output}")


if __name__ == "__main__":
    main()