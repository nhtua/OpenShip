# Standardized Workflow

## Step 1: Ask the user for their name.
- tool: user.ask
- args: what is your name?

## Step 2: Get the current date.
- tool: shell.date
- args: +"%Y-%m-%d"

## Step 3: Greet the user with their name and today's date.
- tool: shell.echo
- args: Hello {step_1}, today is {step_2}

