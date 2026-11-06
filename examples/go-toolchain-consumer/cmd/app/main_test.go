package main

import "testing"

func TestMessage(t *testing.T) {
	if got := message(); got != "go-toolchain-consumer-ok" {
		t.Fatalf("message() = %q, want go-toolchain-consumer-ok", got)
	}
}
