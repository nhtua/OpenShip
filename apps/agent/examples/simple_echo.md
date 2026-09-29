# Simple Echo Workflow

Echo a greeting message.

## Inputs
- name: [required] Name to greet

## Steps
1. Greet the user
   - tool: shell.echo
   - args: Hello {name}! Welcome to OpenShip.