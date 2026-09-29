# Data Flow Workflow

Demonstrate data passing between steps.

## Inputs
- text: [required] Text to process

## Steps
1. Echo input
   - tool: shell.echo
   - args: Input: {text}
2. Count words
   - tool: shell.xargs
   - args: echo {text} | wc -w
3. Show timestamp
   - tool: shell.date
   - args: +"%s"