# Greeting Workflow

Greet the user with their name and today's date.

## Inputs

- name: [required] Your name

## Steps

1. Ask their name
   - tool: user.ask
   - args: what is your name?
2. Get current date
   - tool: shell.date
   - args: +"%Y-%m-%d"
3. Greet the user
   - tools: auto
   - args: Hello {step1}, today is {step2}
