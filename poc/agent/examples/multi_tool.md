# Multi-Tool Workflow

Demonstrate multiple tools in sequence.

## Inputs
- message: [required] Message to echo
- url: [optional] URL to fetch (default: https://example.com)

## Steps
1. Echo the message
   - tool: shell.echo
   - args: Starting: {message}
2. Show current date
   - tool: shell.date
   - args: +"%Y-%m-%d %H:%M:%S"
3. Fetch URL
   - tool: exec.curl
   - args: -s -L {url} | head -n 5
4. Echo completion
   - tool: shell.echo
   - args: Done: {message}