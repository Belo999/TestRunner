package controllers

import (
	"context"
	"fmt"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"

	marathonrunnerv1alpha1 "github.com/marathonrunner/operator/api/v1alpha1"
)

const testRunFinalizer = "marathonrunner.io/testrun-cleanup"

// TestRunReconciler reconciles a TestRun object
type TestRunReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=marathonrunner.io,resources=testruns,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=marathonrunner.io,resources=testruns/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=marathonrunner.io,resources=testruns/finalizers,verbs=update
// +kubebuilder:rbac:groups=batch,resources=jobs,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=pods,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=configmaps,verbs=get;list;watch;create;delete

func (r *TestRunReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Fetch the TestRun instance
	var testRun marathonrunnerv1alpha1.TestRun
	if err := r.Get(ctx, req.NamespacedName, &testRun); err != nil {
		if errors.IsNotFound(err) {
			logger.Info("TestRun resource not found, probably deleted")
			return ctrl.Result{}, nil
		}
		logger.Error(err, "Failed to get TestRun")
		return ctrl.Result{}, err
	}

	// Handle deletion
	if !testRun.ObjectMeta.DeletionTimestamp.IsZero() {
		return r.handleDeletion(ctx, &testRun)
	}

	// Add finalizer if not present
	if !controllerutil.ContainsFinalizer(&testRun, testRunFinalizer) {
		controllerutil.AddFinalizer(&testRun, testRunFinalizer)
		if err := r.Update(ctx, &testRun); err != nil {
			return ctrl.Result{}, err
		}
	}

	// Reconcile based on phase
	switch testRun.Status.Phase {
	case "", marathonrunnerv1alpha1.TestRunPhasePending:
		return r.reconcilePending(ctx, &testRun)
	case marathonrunnerv1alpha1.TestRunPhaseRunning:
		return r.reconcileRunning(ctx, &testRun)
	case marathonrunnerv1alpha1.TestRunPhaseSucceeded, marathonrunnerv1alpha1.TestRunPhaseFailed:
		return r.reconcileTerminal(ctx, &testRun)
	default:
		logger.Info("Unknown phase", "phase", testRun.Status.Phase)
		return ctrl.Result{}, nil
	}
}

func (r *TestRunReconciler) handleDeletion(ctx context.Context, testRun *marathonrunnerv1alpha1.TestRun) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	if controllerutil.ContainsFinalizer(testRun, testRunFinalizer) {
		// Clean up the Job
		if testRun.Status.JobName != "" {
			var job batchv1.Job
			err := r.Get(ctx, types.NamespacedName{
				Name:      testRun.Status.JobName,
				Namespace: testRun.Namespace,
			}, &job)
			if err == nil {
				if err := r.Delete(ctx, &job); err != nil && !errors.IsNotFound(err) {
					logger.Error(err, "Failed to delete Job during cleanup")
					return ctrl.Result{}, err
				}
			}
		}

		controllerutil.RemoveFinalizer(testRun, testRunFinalizer)
		if err := r.Update(ctx, testRun); err != nil {
			return ctrl.Result{}, err
		}
	}

	return ctrl.Result{}, nil
}

func (r *TestRunReconciler) reconcilePending(ctx context.Context, testRun *marathonrunnerv1alpha1.TestRun) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	logger.Info("Creating Job for TestRun", "runId", testRun.Spec.RunID, "engine", testRun.Spec.Engine)

	// Build the Job spec from the engine configuration
	job, err := r.buildJob(testRun)
	if err != nil {
		logger.Error(err, "Failed to build Job spec")
		return r.updateStatus(ctx, testRun, marathonrunnerv1alpha1.TestRunPhaseFailed, err.Error())
	}

	// Create the Job
	if err := r.Create(ctx, job); err != nil {
		if errors.IsAlreadyExists(err) {
			logger.Info("Job already exists, updating status")
		} else {
			logger.Error(err, "Failed to create Job")
			return r.updateStatus(ctx, testRun, marathonrunnerv1alpha1.TestRunPhaseFailed, fmt.Sprintf("Failed to create Job: %v", err))
		}
	}

	// Update status to Pending with Job name
	now := metav1.Now()
	testRun.Status.Phase = marathonrunnerv1alpha1.TestRunPhasePending
	testRun.Status.JobName = job.Name
	testRun.Status.StartTime = &now
	testRun.Status.ObservedGeneration = testRun.Generation
	testRun.Status.Message = "Job created, waiting for pods to start"

	r.setCondition(testRun, "EngineLaunched", "True", "JobCreated", "Job has been created successfully")

	if err := r.Status().Update(ctx, testRun); err != nil {
		return ctrl.Result{}, err
	}

	return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
}

func (r *TestRunReconciler) reconcileRunning(ctx context.Context, testRun *marathonrunnerv1alpha1.TestRun) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	if testRun.Status.JobName == "" {
		logger.Info("No Job name in status, requeuing")
		return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
	}

	// Get the Job
	var job batchv1.Job
	if err := r.Get(ctx, types.NamespacedName{
		Name:      testRun.Status.JobName,
		Namespace: testRun.Namespace,
	}, &job); err != nil {
		if errors.IsNotFound(err) {
			logger.Info("Job not found, marking as failed")
			return r.updateStatus(ctx, testRun, marathonrunnerv1alpha1.TestRunPhaseFailed, "Job was deleted unexpectedly")
		}
		return ctrl.Result{}, err
	}

	// Check Job status
	if job.Status.Succeeded > 0 {
		logger.Info("Job succeeded")
		now := metav1.Now()
		testRun.Status.Phase = marathonrunnerv1alpha1.TestRunPhaseSucceeded
		testRun.Status.CompletionTime = &now
		testRun.Status.Message = "Test completed successfully"
		r.setCondition(testRun, "Complete", "True", "JobSucceeded", "All pods completed successfully")
		if err := r.Status().Update(ctx, testRun); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	if job.Status.Failed > 0 {
		logger.Info("Job failed")
		now := metav1.Now()
		testRun.Status.Phase = marathonrunnerv1alpha1.TestRunPhaseFailed
		testRun.Status.CompletionTime = &now
		testRun.Status.Message = "Test failed"
		r.setCondition(testRun, "Complete", "True", "JobFailed", "One or more pods failed")
		if err := r.Status().Update(ctx, testRun); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	// Check for timeout
	if testRun.Status.StartTime != nil {
		elapsed := time.Since(testRun.Status.StartTime.Time)
		maxDuration := time.Duration(testRun.Spec.DurationMinutes)*time.Minute + 5*time.Minute // 5 min grace
		if elapsed > maxDuration {
			logger.Info("Test timed out, deleting Job")
			if err := r.Delete(ctx, &job); err != nil && !errors.IsNotFound(err) {
				return ctrl.Result{}, err
			}
			now := metav1.Now()
			testRun.Status.Phase = marathonrunnerv1alpha1.TestRunPhaseFailed
			testRun.Status.CompletionTime = &now
			testRun.Status.Message = "Test timed out"
			r.setCondition(testRun, "TimedOut", "True", "Timeout", "Exceeded maximum duration")
			if err := r.Status().Update(ctx, testRun); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{}, nil
		}
	}

	// Still running, requeue
	return ctrl.Result{RequeueAfter: 15 * time.Second}, nil
}

func (r *TestRunReconciler) reconcileTerminal(ctx context.Context, testRun *marathonrunnerv1alpha1.TestRun) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// If retention period has passed, the Job should be cleaned up
	if testRun.Status.CompletionTime != nil && testRun.Status.JobName != "" {
		retention := time.Duration(testRun.Spec.RetentionMinutes) * time.Minute
		if retention == 0 {
			retention = 60 * time.Minute
		}
		elapsed := time.Since(testRun.Status.CompletionTime.Time)
		if elapsed > retention {
			var job batchv1.Job
			err := r.Get(ctx, types.NamespacedName{
				Name:      testRun.Status.JobName,
				Namespace: testRun.Namespace,
			}, &job)
			if err == nil {
				logger.Info("Cleaning up completed Job", "job", job.Name)
				if err := r.Delete(ctx, &job); err != nil && !errors.IsNotFound(err) {
					return ctrl.Result{}, err
				}
			}
		}
	}

	// No requeue for terminal states
	return ctrl.Result{}, nil
}

func (r *TestRunReconciler) buildJob(testRun *marathonrunnerv1alpha1.TestRun) (*batchv1.Job, error) {
	jobName := fmt.Sprintf("mr-run-%d-%s", testRun.Spec.RunID, testRun.Spec.Engine)

	// Determine engine image
	image := getEngineImage(testRun.Spec.Engine)
	if image == "" {
		return nil, fmt.Errorf("unknown engine: %s", testRun.Spec.Engine)
	}

	// Build command and args based on engine
	command, args := getEngineCommand(testRun.Spec.Engine, testRun)

	// Build environment variables
	envVars := []corev1.EnvVar{
		{Name: "TARGET_ENDPOINT", Value: testRun.Spec.TargetEndpoint},
		{Name: "VUS", Value: fmt.Sprintf("%d", testRun.Spec.TargetVusers)},
		{Name: "DURATION", Value: fmt.Sprintf("%ds", testRun.Spec.DurationMinutes*60)},
	}

	// Default resource requirements
	resources := corev1.ResourceRequirements{
		Requests: corev1.ResourceList{
			corev1.ResourceCPU:    mustParseQuantity("500m"),
			corev1.ResourceMemory: mustParseQuantity("512Mi"),
		},
		Limits: corev1.ResourceList{
			corev1.ResourceCPU:    mustParseQuantity("2"),
			corev1.ResourceMemory: mustParseQuantity("2Gi"),
		},
	}
	if testRun.Spec.Resources != nil {
		resources = *testRun.Spec.Resources
	}

	// Build labels
	labels := map[string]string{
		"marathonrunner.io/run-id":   fmt.Sprintf("%d", testRun.Spec.RunID),
		"marathonrunner.io/engine":   testRun.Spec.Engine,
		"marathonrunner.io/managed":  "true",
	}
	for k, v := range testRun.Spec.Labels {
		labels[k] = v
	}

	backoffLimit := int32(0)
	ttlSeconds := int32(testRun.Spec.RetentionMinutes * 60)
	if ttlSeconds == 0 {
		ttlSeconds = 3600
	}

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      jobName,
			Namespace: testRun.Namespace,
			Labels:    labels,
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoffLimit,
			TTLSecondsAfterFinished: &ttlSeconds,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: labels,
				},
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					Containers: []corev1.Container{
						{
							Name:    testRun.Spec.Engine,
							Image:   image,
							Command: command,
							Args:    args,
							Env:     envVars,
							VolumeMounts: []corev1.VolumeMount{
								{
									Name:      "scripts",
									MountPath: "/scripts",
									ReadOnly:  true,
								},
								{
									Name:      "results",
									MountPath: "/results",
								},
							},
							Resources: resources,
						},
					},
					Volumes: []corev1.Volume{
						{
							Name: "scripts",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: testRun.Spec.ScriptConfigMap,
									},
								},
							},
						},
						{
							Name: "results",
							VolumeSource: corev1.VolumeSource{
								EmptyDir: &corev1.EmptyDirVolumeSource{},
							},
						},
					},
				},
			},
		},
	}

	return job, nil
}

func getEngineImage(engine string) string {
	images := map[string]string{
		"k6":        "grafana/k6:latest",
		"JMeter":    "justb4/jmeter:latest",
		"Gatling":   "denvazh/gatling:latest",
		"Locust":    "locustio/locust:latest",
		"Playwright": "mcr.microsoft.com/playwright:latest",
	}
	return images[engine]
}

func getEngineCommand(engine string, testRun *marathonrunnerv1alpha1.TestRun) ([]string, []string) {
	switch engine {
	case "k6":
		return []string{"k6", "run", "--summary-export=/results/summary.json", "/scripts/test.js"}, nil
	case "JMeter":
		return []string{"jmeter", "-n",
			"-t", "/scripts/test-plan.jmx",
			"-Jthreads", fmt.Sprintf("%d", testRun.Spec.TargetVusers),
			"-Jduration", fmt.Sprintf("%d", testRun.Spec.DurationMinutes*60),
			"-l", "/results/results.jtl",
			"-e", "-o", "/results/report"}, nil
	case "Gatling":
		return []string{"gatling", "-nr", "-s", "Simulation"}, nil
	case "Locust":
		return []string{"locust", "-f", "/scripts/test.py",
			"--host", testRun.Spec.TargetEndpoint,
			"--headless",
			"-u", fmt.Sprintf("%d", testRun.Spec.TargetVusers),
			"-r", "10",
			"--run-time", fmt.Sprintf("%dm", testRun.Spec.DurationMinutes)}, nil
	case "Playwright":
		return []string{"npx", "playwright", "test"}, nil
	default:
		return nil, nil
	}
}

func (r *TestRunReconciler) updateStatus(ctx context.Context, testRun *marathonrunnerv1alpha1.TestRun, phase marathonrunnerv1alpha1.TestRunPhase, message string) (ctrl.Result, error) {
	testRun.Status.Phase = phase
	testRun.Status.Message = message
	testRun.Status.ObservedGeneration = testRun.Generation
	if err := r.Status().Update(ctx, testRun); err != nil {
		return ctrl.Result{}, err
	}
	if phase == marathonrunnerv1alpha1.TestRunPhaseFailed {
		return ctrl.Result{}, nil
	}
	return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
}

func (r *TestRunReconciler) setCondition(testRun *marathonrunnerv1alpha1.TestRun, condType, status, reason, message string) {
	for i, c := range testRun.Status.Conditions {
		if c.Type == condType {
			testRun.Status.Conditions[i].Status = status
			testRun.Status.Conditions[i].LastTransitionTime = metav1.Now()
			testRun.Status.Conditions[i].Reason = reason
			testRun.Status.Conditions[i].Message = message
			return
		}
	}
	testRun.Status.Conditions = append(testRun.Status.Conditions, marathonrunnerv1alpha1.TestRunCondition{
		Type:               condType,
		Status:             status,
		LastTransitionTime: metav1.Now(),
		Reason:             reason,
		Message:            message,
	})
}

func mustParseQuantity(s string) resource.Quantity {
	q, err := resource.ParseQuantity(s)
	if err != nil {
		panic(fmt.Sprintf("invalid resource quantity %q: %v", s, err))
	}
	return q
}

// SetupWithManager sets up the controller with the Manager.
func (r *TestRunReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&marathonrunnerv1alpha1.TestRun{}).
		Owns(&batchv1.Job{}).
		Complete(r)
}
