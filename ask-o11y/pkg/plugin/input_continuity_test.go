package plugin

import (
	"consensys-asko11y-app/pkg/agent"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSessionHistoryPreservesErrorsAndUnavailableResults(t *testing.T) {
	args := `{"python_code":"print('Country-A')","frame_ref":"artifact://input/grafana-frame"}`
	failure := `{"ok":false,"error":"report rejected","evidence":{"execution_ref":"artifact://result/sandbox-execution"}}`
	raw, _ := json.Marshal([]map[string]interface{}{
		{"name": "sandbox-analysis_execute_python_analysis", "arguments": args, "error": failure},
		{"name": "interrupted", "running": true},
	})
	messages := restoreSessionMessages([]SessionMessage{{Role: "assistant", ToolCalls: raw}})
	if len(messages) != 5 || messages[0].ToolCalls[0].Function.Arguments != args || messages[1].Content != "[Tool failed]\n"+failure {
		t.Fatalf("prior failure lost: %#v", messages)
	}
	if messages[0].ToolCalls[0].ID != messages[1].ToolCallID || messages[1].ToolCallID == messages[3].ToolCallID || !strings.Contains(messages[3].Content, "unavailable") {
		t.Fatal("legacy pairing or unfinished call handling is invalid")
	}
	for _, msg := range messages {
		if msg.Role == "system" {
			t.Fatal("tool data elevated to system instructions")
		}
	}
}

func TestHandleAgentRunKeepsTaskModelAndFailureContext(t *testing.T) {
	for _, approval := range []string{"授權執行 Python 視覺分析", "同意"} {
		t.Run(approval, func(t *testing.T) {
			llm, received := newAgentRunLLMServer(t)
			defer llm.Close()
			p := newAgentRunTestPlugin(t)
			code := "print('prior analysis')"
			args, _ := json.Marshal(map[string]string{"python_code": code, "frame_ref": "artifact://input/grafana-frame"})
			calls, _ := json.Marshal([]map[string]interface{}{{"name": "sandbox-analysis_execute_python_analysis", "arguments": string(args), "error": "report rejected, keep execution_ref"}})
			session, err := p.sessionStore.CreateSession(7, 2, "analysis", []SessionMessage{
				{Role: "user", Content: "確認執行並產出dashboard preview"},
				{Role: "assistant", Content: "The report failed.", ToolCalls: calls},
			})
			if err != nil {
				t.Fatal(err)
			}
			body, _ := json.Marshal(map[string]string{"message": approval, "type": "chat", "sessionId": session.ID})
			req := newAgentRunRequest(t, llm.URL, "/api/agent/run", string(body))
			rec := httptest.NewRecorder()
			p.handleAgentRun(rec, req)
			if rec.Code != http.StatusOK {
				t.Fatalf("handler: %d %s", rec.Code, rec.Body.String())
			}
			var response struct {
				Model string `json:"model"`
			}
			_ = json.Unmarshal(rec.Body.Bytes(), &response)
			if response.Model != "large" {
				t.Fatalf("short approval downgraded task: %s", response.Model)
			}
			observed := receiveAgentRunLLMRequest(t, received)
			if observed.Model != "large" {
				t.Fatalf("actual LLM request downgraded: %s", observed.Model)
			}
			// Verify the actual model input, not just the HTTP response or helper output.
			encoded, _ := json.Marshal(observed.Messages)
			if !strings.Contains(string(encoded), "report rejected") || !strings.Contains(string(encoded), "prior analysis") {
				t.Fatalf("actual model input lost repair context: %s", encoded)
			}
		})
	}
}

func TestPriorDeclaredFailureNeverBecomesSuccessfulWriterState(t *testing.T) {
	for _, response := range []string{
		`{"content":[{"type":"text","text":"{\"ok\":false,\"uid\":\"not-saved\"}"}]}`,
		`{"isError":true,"content":[{"type":"text","text":"{\"uid\":\"not-saved\"}"}]}`,
	} {
		raw := json.RawMessage(`[{"name":"mcp-grafana_update_dashboard","response":` + response + `}]`)
		messages := restoreSessionMessages([]SessionMessage{{Role: "assistant", ToolCalls: raw}})
		if len(messages) != 3 || messages[1].Role != "tool" || (!strings.Contains(messages[1].Content, `"ok":false`) && !strings.Contains(messages[1].Content, "[Tool failed]")) {
			t.Fatalf("failed writer lost its failure status: %#v", messages)
		}
	}
}

func TestHandleAgentRunPreservesActualReportToolMessages(t *testing.T) {
	llm, received := newAgentRunLLMServer(t)
	defer llm.Close()
	p := newAgentRunTestPlugin(t)
	// A successful report larger than the old 16 KiB whitelist, but within the normal context budget.
	result := `{"ok":true,"evidence":{"remaining_artifact_count":0},"refs":{"inspection_ref":"artifact://report/report-inspection"},"report_context":{"facts":{"heat_rate":9163.2}},"inspection":{"figure":"` + strings.Repeat("retained-observation ", 7000) + `"}}`
	arguments := `{"report_manifest_ref":"artifact://report/report-manifest"}`
	stored := reconstructAssistantMessage([]agent.SSEEvent{
		{Type: "tool_call_start", Data: agent.ToolCallStartEvent{ID: "inspect-original", Name: "artifact-bridge_inspect_report_artifacts", Arguments: arguments}},
		{Type: "tool_call_result", Data: agent.ToolCallResultEvent{ID: "inspect-original", Name: "artifact-bridge_inspect_report_artifacts", Content: result}},
		{Type: "content", Data: agent.ContentEvent{Content: "The report is ready."}},
	})
	session, err := p.sessionStore.CreateSession(7, 2, "report", []SessionMessage{{Role: "user", Content: "Create a dashboard preview"}, stored})
	if err != nil {
		t.Fatal(err)
	}
	rec := httptest.NewRecorder()
	p.handleAgentRun(rec, newAgentRunRequest(t, llm.URL, "/api/agent/run", fmt.Sprintf(`{"message":"繼續","sessionId":%q}`, session.ID)))
	if rec.Code != http.StatusOK {
		t.Fatalf("handler: %d %s", rec.Code, rec.Body.String())
	}
	request := receiveAgentRunLLMRequest(t, received)
	for i, msg := range request.Messages {
		if msg.Role != "tool" || msg.Content != result {
			continue
		}
		if i == 0 || msg.ToolCallID != "inspect-original" {
			t.Fatal("tool result lost its original call identity")
		}
		calls := request.Messages[i-1].ToolCalls
		if len(calls) != 1 || calls[0].ID != msg.ToolCallID || calls[0].Function.Arguments != arguments {
			t.Fatal("orphaned/rewritten tool call")
		}
		return
	}
	t.Fatal("actual provider request lost report facts, figures and completion status; refs-only history is not evidence")
}

func TestTaskHistoryCannotOverrideExplicitBase(t *testing.T) {
	for _, stored := range []bool{false, true} {
		t.Run(fmt.Sprint(stored), func(t *testing.T) {
			llm, received := newAgentRunLLMServer(t)
			defer llm.Close()
			p := newAgentRunTestPlugin(t)
			session, err := p.sessionStore.CreateSession(7, 2, "analysis", []SessionMessage{{Role: "user", Content: "Create a dashboard"}})
			if err != nil {
				t.Fatal(err)
			}
			target := "/api/agent/run?model=base"
			if stored {
				model := "base"
				if err := p.sessionStore.UpdateSession(session.ID, 7, 2, SessionUpdate{Model: &model}); err != nil {
					t.Fatal(err)
				}
				target = "/api/agent/run"
			}
			req := newAgentRunRequest(t, llm.URL, target, fmt.Sprintf(`{"message":"同意","sessionId":%q}`, session.ID))
			rec := httptest.NewRecorder()
			p.handleAgentRun(rec, req)
			if rec.Code != http.StatusOK {
				t.Fatalf("handler: %d %s", rec.Code, rec.Body.String())
			}
			if actual := receiveAgentRunLLMRequest(t, received); actual.Model != "base" {
				t.Fatalf("explicit base overridden: %s", actual.Model)
			}
		})
	}
}
