package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// TestRunPhase represents the current phase of a TestRun
type TestRunPhase string

const (
	TestRunPhasePending   TestRunPhase = "Pending"
	TestRunPhaseRunning   TestRunPhase = "Running"
	TestRunPhaseSucceeded TestRunPhase = "Succeeded"
	TestRunPhaseFailed    TestRunPhase = "Failed"
)

// TestRunSpec defines the desired state of TestRun
type TestRunSpec struct {
	// RunID is the MarathonRunner database run ID
	// +kubebuilder:validation:Required
	RunID int64 `json:"runId"`

	// Engine is the test engine adapter name (k6, JMeter, Gatling, Locust, Playwright)
	// +kubebuilder:validation:Enum=k6;JMeter;Gatling;Locust;Playwright
	// +kubebuilder:validation:Required
	Engine string `json:"engine"`

	// TargetEndpoint is the URL of the system under test
	// +kubebuilder:validation:Required
	TargetEndpoint string `json:"targetEndpoint"`

	// TargetVusers is the number of virtual users to simulate
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Required
	TargetVusers int32 `json:"targetVusers"`

	// DurationMinutes is the test duration in minutes
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Required
	DurationMinutes int32 `json:"durationMinutes"`

	// LoadProfile is the load profile type (constant, ramping, spike, etc.)
	// +kubebuilder:default="constant"
	// +optional
	LoadProfile string `json:"loadProfile,omitempty"`

	// ScriptConfigMap is the name of the ConfigMap containing test scripts
	// +kubebuilder:validation:Required
	ScriptConfigMap string `json:"scriptConfigMap"`

	// ResultVolume is the name of the volume for storing test results
	// +optional
	ResultVolume string `json:"resultVolume,omitempty"`

	// Resources defines the resource requests and limits for the engine container
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`

	// Labels to apply to the Job and Pod
	// +optional
	Labels map[string]string `json:"labels,omitempty"`

	// Namespace overrides the default execution namespace
	// +kubebuilder:default="marathonrunner-execution"
	// +optional
	Namespace string `json:"namespace,omitempty"`

	// RetentionMinutes defines how long to keep the Job after completion (default 60)
	// +kubebuilder:default=60
	// +optional
	RetentionMinutes int32 `json:"retentionMinutes,omitempty"`
}

// TestRunResults holds the parsed test results after completion
type TestRunResults struct {
	P50Ms           int32   `json:"p50Ms,omitempty"`
	P95Ms           int32   `json:"p95Ms,omitempty"`
	P99Ms           int32   `json:"p99Ms,omitempty"`
	ThroughputRps   float64 `json:"throughputRps,omitempty"`
	ErrorRate       float64 `json:"errorRate,omitempty"`
	TotalRequests   int64   `json:"totalRequests,omitempty"`
	FailedRequests  int64   `json:"failedRequests,omitempty"`
	DurationSeconds float64 `json:"durationSeconds,omitempty"`
}

// TestRunCondition describes a condition of a TestRun
type TestRunCondition struct {
	// Type is the type of condition
	Type string `json:"type"`

	// Status is the status of the condition (True, False, Unknown)
	Status string `json:"status"`

	// LastTransitionTime is the last time the condition transitioned
	LastTransitionTime metav1.Time `json:"lastTransitionTime"`

	// Reason is a machine-readable reason for the condition
	// +optional
	Reason string `json:"reason,omitempty"`

	// Message is a human-readable message indicating details
	// +optional
	Message string `json:"message,omitempty"`
}

// TestRunStatus defines the observed state of TestRun
type TestRunStatus struct {
	// Phase is the current lifecycle phase (Pending, Running, Succeeded, Failed)
	// +kubebuilder:validation:Enum=Pending;Running;Succeeded;Failed
	// +optional
	Phase TestRunPhase `json:"phase,omitempty"`

	// StartTime is when the test Job was created
	// +optional
	StartTime *metav1.Time `json:"startTime,omitempty"`

	// CompletionTime is when the test Job completed
	// +optional
	CompletionTime *metav1.Time `json:"completionTime,omitempty"`

	// JobName is the name of the Kubernetes Job created for this run
	// +optional
	JobName string `json:"jobName,omitempty"`

	// Conditions represent the latest available observations of the object's state
	// +optional
	Conditions []TestRunCondition `json:"conditions,omitempty"`

	// Results holds the parsed test results after completion
	// +optional
	Results *TestRunResults `json:"results,omitempty"`

	// ObservedGeneration is the most recent generation observed
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Message is a human-readable status message
	// +optional
	Message string `json:"message,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Engine",type=string,JSONPath=`.spec.engine`
// +kubebuilder:printcolumn:name="VUsers",type=integer,JSONPath=`.spec.targetVusers`
// +kubebuilder:printcolumn:name="Duration",type=integer,JSONPath=`.spec.durationMinutes`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// TestRun is the Schema for the testruns API
type TestRun struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   TestRunSpec   `json:"spec,omitempty"`
	Status TestRunStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// TestRunList contains a list of TestRun
type TestRunList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []TestRun `json:"items"`
}

func init() {
	SchemeBuilder.Register(&TestRun{}, &TestRunList{})
}
