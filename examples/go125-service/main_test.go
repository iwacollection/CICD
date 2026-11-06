package main
import "testing"
func TestConsumerBuild(t *testing.T) {
    if got := message(); got != "go125-consumer-ok" { t.Fatalf("unexpected message: %s", got) }
}
