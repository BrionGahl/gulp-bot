"""The Discord side of the bot (the controllers): one module per group of commands, and one per kind of
event. Each reads what the member gave, checks they may, calls a service, and answers with what a mapper
made of the result. `registry.py` adds them all to the bot."""
