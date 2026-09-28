# Standardized Workflow

## Step 1: Ask the user for their name.
- tool: user.ask
- args: {'question': 'What is your name?'}

## Step 2: Get today's date in a human-readable format.
- tool: shell.date
- args: {'format': '+%A, %B %d, %Y'}

## Step 3: Greet the user with their name and today's date.
- tool: shell.echo
- args: {'text': 'Hello, {step_1}! Today is {step_2}.'}

