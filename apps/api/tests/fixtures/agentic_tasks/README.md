"""Controlled inference fixtures: the answers a synthetic provider returns.

Each file is exactly the text one bounded stage answers with, so a test case
states what the provider said and the runtime's validation is what is under
test. The malformed file is not valid JSON on purpose: duplicate keys are a
refusal, and one bad answer has to cost a real attempt.
"""
