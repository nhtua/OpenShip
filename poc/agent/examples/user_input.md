# User Input Workflow

Demonstrate user input and data flow between steps.

## Steps
1. Ask user for their name
   - tool: user.ask
   - args: What is your name?
2. Ask user for their city
   - tool: user.ask
   - args: What city do you live in?
3. Greet user with their info
   - tool: shell.echo
   - args: Hello {step_1} from {step_2}! Welcome to OpenShip.