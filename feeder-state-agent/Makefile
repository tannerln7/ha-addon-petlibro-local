CC ?= gcc
CFLAGS ?= -Os -Wall -Wextra -std=c99
LDFLAGS ?= -static
BINARY ?= plaf203-state-agent

all: $(BINARY)

$(BINARY): plaf203_state_agent.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(BINARY)

.PHONY: all clean
