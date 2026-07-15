package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// PerformanceTestPhase represents the current phase of a PerformanceTest
type PerformanceTestPhase string

const (
	PerformanceTestPhasePending    PerformanceTestPhase = "Pending"
	PerformanceTestPhaseInProgress PerformanceTestPhase = "InProgress"
	PerformanceTestPhaseSucceeded  PerformanceTestPhase = "Succeeded"
	PerformanceTestPhaseFailed     PerformanceTestPhase = "Failed"
)

// PerformanceTestRun defines a single run within a PerformanceTest
type PerformanceTestRun struct {
	// Name is a descriptive name for this run
	// +kubebuilder:validation:Required
	Name string `json:"name"`

	// Engine is the test engine adapter name
	// +kubebuilder:validation:Enum=k6;JMeter;Gatling;Locust;Playwright
	// +kubebuilder:validation:Required
	Engine string `json:"engine"`

	// TargetEndpoint is the URL of the system under test
	// +optional
	TargetEndpoint string `json:"targetEndpoint,omitempty"`

	// TargetVusers is the number of virtual users to simulate
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Required
	TargetVusers int32 `json:"targetVusers"`

	// DurationMinutes is the test duration in minutes
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Required
	DurationMinutes int32 `json:"durationMinutes"`

	// LoadProfile is the load profile type
	// +kubebuilder:default="constant"
	// +optional
	LoadProfile string `json:"loadProfile,omitempty"`
}

// ExecutionWindow defines when a test is allowed to execute
type ExecutionWindow struct {
	// StartHour is the start of the execution window (0-23)
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=23
	// +optional
	StartHour int32 `json:"startHour,omitempty"`

	// EndHour is the end of the execution window (0-23)
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=23
	// +optional
	EndHour int32 `json:"endHour,omitempty"`

	// Timezone is the IANA timezone name
	// +kubebuilder:default="UTC"
	// +optional
	Timezone string `json:"timezone,omitempty"`
}

// PerformanceTestPolicy defines governance policies for a PerformanceTest
type PerformanceTestPolicy struct {
	// ApprovalRequired indicates if manual approval is needed before execution
	// +kubebuilder:default=false
	// +optional
	ApprovalRequired bool `json:"approvalRequired,omitempty"`

	// MaxRiskScore is the maximum acceptable risk score (0-100)
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	// +kubebuilder:default=100
	// +optional
	MaxRiskScore int32 `json:"maxRiskScore,omitempty"`

	// ExecutionWindow defines when the test can run
	// +optional
	ExecutionWindow *ExecutionWindow `json:"executionWindow,omitempty"`
}

// PerformanceTestSpec defines the desired state of PerformanceTest
type PerformanceTestSpec struct {
	// ProjectID is the MarathonRunner project ID
	// +kubebuilder:validation:Required
	ProjectID int64 `json:"projectId"`

	// EnvironmentID is the target environment ID
	// +kubebuilder:validation:Required
	EnvironmentID int64 `json:"environmentId"`

	// Runs defines the set of test runs to execute
	// +kubebuilder:validation:MinItems=1
	// +kubebuilder:validation:Required
	Runs []PerformanceTestRun `json:"runs"`

	// Policy defines governance policies
	// +optional
	Policy *PerformanceTestPolicy `json:"policy,omitempty"`
}

// PerformanceTestRunStatus holds the status of a single run within a PerformanceTest
type PerformanceTestRunStatus struct {
	// Name matches the run definition
	Name string `json:"name"`

	// RunRef is the name of the TestRun CR created for this run
	// +optional
	RunRef string `json:"runRef,omitempty"`

	// Status is the current status of the run (Pending, Running, Succeeded, Failed)
	Status string `json:"status"`
}

// PerformanceTestStatus defines the observed state of PerformanceTest
type PerformanceTestStatus struct {
	// Phase is the current lifecycle phase
	// +optional
	Phase PerformanceTestPhase `json:"phase,omitempty"`

	// StartTime is when the first run was created
	// +optional
	StartTime *metav1.Time `json:"startTime,omitempty"`

	// CompletionTime is when all runs completed
	// +optional
	CompletionTime *metav1.Time `json:"completionTime,omitempty"`

	// Runs contains the status of each run
	// +optional
	Runs []PerformanceTestRunStatus `json:"runs,omitempty"`

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
// +kubebuilder:printcolumn:name="Runs",type=integer,JSONPath=`.spec.runs`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// PerformanceTest is the Schema for the performancetests API
type PerformanceTest struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   PerformanceTestSpec   `json:"spec,omitempty"`
	Status PerformanceTestStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// PerformanceTestList contains a list of PerformanceTest
type PerformanceTestList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []PerformanceTest `json:"items"`
}

func init() {
	SchemeBuilder.Register(&PerformanceTest{}, &PerformanceTestList{})
}
