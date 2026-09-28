# Create a greeting workflow that asks the user for their name, gets today's date, and then greets them with their name and the date.

## Step 1: Ask the user for their name.
- tool: user.ask
- args: {'question': 'What is your name?'}

## Step 2: Get today's date in a readable format.
- tool: shell.date
- args: {'format string quoted': '+%B %d, %Y'}

## Step 3: Greet the user with their name and today's date.
- tool: shell.echo
- args: {'text to print': "Hello, {step_1}! Today's date is {step_2}."}

