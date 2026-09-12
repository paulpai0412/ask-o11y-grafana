package mcp

import (
	"net/http"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) { return fn(req) }

func TestCustomRoundTripperAddsTrustedSessionHeader(t *testing.T) {
	base := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if got := req.Header.Get("X-Grafana-Session-Id"); got != "session-123" {
			t.Fatalf("session header = %q", got)
		}
		return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: http.NoBody, Request: req}, nil
	})
	transport := &customRoundTripper{base: base, sessionID: "session-123"}
	request, err := http.NewRequest(http.MethodPost, "http://example.invalid/mcp", nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := transport.RoundTrip(request); err != nil {
		t.Fatal(err)
	}
}
