# Greeting Workflow

Greet the user with their name and today's date.

## Inputs
- name: [required] Your name

## Steps
1. Get current date
   - tool: shell.date
   - args: +"%Y-%m-%d"
2. Greet the user
   - tool: shell.echo
   - args: Hello {name}, today is {previous_output}