# Terminal work in a container, graded by the machine's final state

An agent is dropped into a Linux container with one instruction and a shell. It has to actually
do the thing — build an extension, configure a service, fix a vulnerability, extract a summary
from logs — and it is graded afterwards by a script that inspects the machine, not by anything
the agent says. Talking about the work scores zero; only the resulting state counts.

The agent runs a turn loop: it issues a command, sees the terminal output, and decides what to do
next, until it declares itself finished or runs out of turns. Nobody checks its reasoning. The
tasks are heterogeneous — different languages, different tools, different definitions of done —
so nothing that works here can be specific to one of them.

What is being written is the **standing operating instruction** that agent carries into every one
of those containers: how it should approach an unfamiliar machine, in what order, how carefully,
how it decides it is done. It is read once, before the agent acts, and it cannot mention any
particular task because it is used on all of them.

The pressures are real and they pull against each other. Being thorough costs turns, and turns
are capped; an agent that inspects everything runs out before it fixes anything. Being fast
means acting on assumptions about a machine it has not looked at. Retrying a failed command
unchanged wastes the budget that would have paid for reading the error. Declaring victory
without checking scores exactly zero, however much good work preceded it.
