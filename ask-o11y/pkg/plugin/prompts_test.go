package plugin

import (
	"strings"
	"testing"
)

func TestBuildSystemPrompt_InvestigationAppendsAddendum(t *testing.T) {
	r, err := NewPromptRegistry(PluginSettings{})
	if err != nil {
		t.Fatal(err)
	}
	base, err := r.BuildSystemPrompt(BuildToolContext("Org1", "Admin"))
	if err != nil {
		t.Fatal(err)
	}
	ctx := BuildToolContext("Org1", "Admin")
	ctx.ConversationType = "investigation"
	withAdd, err := r.BuildSystemPrompt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(withAdd, DefaultInvestigationModeSystemAddendum) {
		t.Fatal("expected investigation system addendum in prompt")
	}
	if len(withAdd) <= len(base) {
		t.Fatalf("investigation prompt should be longer than base; base=%d with=%d", len(base), len(withAdd))
	}
}

func TestBuildSystemPrompt_EvidenceAndUnknownOutcome(t *testing.T) {
	r, err := NewPromptRegistry(PluginSettings{})
	if err != nil {
		t.Fatal(err)
	}
	out, err := r.BuildSystemPrompt(BuildToolContext("Org1", "Viewer"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "Do not fabricate results") {
		t.Fatal("expected evidence-only instruction in base prompt")
	}
	if !strings.Contains(out, "do not retry or submit replacement code while its outcome is unknown") {
		t.Fatal("expected unknown-outcome safety instruction")
	}
}

func TestBuildSystemPrompt_FeedbackGuardrailsPresent(t *testing.T) {
	r, err := NewPromptRegistry(PluginSettings{})
	if err != nil {
		t.Fatal(err)
	}
	out, err := r.BuildSystemPrompt(BuildToolContext("Org1", "Editor"))
	if err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		name    string
		snippet string
	}{
		{"no unprompted writes", "without explicit user authorization"},
		{"capability honesty", "current tool schemas and actual results"},
		{"honor user time range", "user's actual time range"},
	}
	for _, tc := range cases {
		if !strings.Contains(out, tc.snippet) {
			t.Errorf("expected %q guardrail (%q) in system prompt", tc.name, tc.snippet)
		}
	}
}

func TestBuildSystemPrompt_DatasourceSnapshotSlot(t *testing.T) {
	r, err := NewPromptRegistry(PluginSettings{})
	if err != nil {
		t.Fatal(err)
	}

	// Without snapshot -> no "Known Datasource UIDs" block.
	blank, err := r.BuildSystemPrompt(BuildToolContext("Org1", "Admin"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(blank, "Known Datasource UIDs (this run)") {
		t.Fatal("empty DatasourceSnapshot should not render the UIDs block")
	}

	// With snapshot -> block rendered verbatim.
	ctx := BuildToolContext("Org1", "Admin")
	ctx.DatasourceSnapshot = "- prometheus (mimir): uid=abc123\n- loki (loki-prod): uid=def456"
	withSnap, err := r.BuildSystemPrompt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(withSnap, "Known Datasource UIDs (this run)") {
		t.Fatal("expected Known Datasource UIDs block when snapshot is set")
	}
	if !strings.Contains(withSnap, "uid=abc123") || !strings.Contains(withSnap, "uid=def456") {
		t.Fatal("expected snapshot UIDs rendered inside the block")
	}
}

func TestBuildSystemPromptLoadsAdvisorySkillAndPreservesCustomPrompt(t *testing.T) {
	for _, custom := range []string{"", "My saved prompt; keep it unchanged."} {
		settings := PluginSettings{DefaultSystemPrompt: custom}
		registry, err := NewPromptRegistry(settings)
		if err != nil {
			t.Fatal(err)
		}
		out, err := registry.BuildSystemPrompt(BuildToolContext("Org1", "Editor"))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(out, analysisSkill) || !strings.Contains(out, "advisory, not an approval or workflow") {
			t.Fatal("skill is not in the actual system prompt")
		}
		if settings.DefaultSystemPrompt != custom || (custom != "" && !strings.HasPrefix(out, custom)) {
			t.Fatal("saved custom prompt was replaced")
		}
		if custom == "" && !strings.Contains(out, "query_dataset") {
			t.Fatal("new users did not receive the minimal analyst prompt")
		}
	}
}
