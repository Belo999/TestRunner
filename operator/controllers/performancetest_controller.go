package controllers

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"

	marathonrunnerv1alpha1 "github.com/marathonrunner/operator/api/v1alpha1"
)

const performanceTestFinalizer = "marathonrunner.io/performancetest-cleanup"

// PerformanceTestReconciler reconciles a PerformanceTest object
type PerformanceTestReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=marathonrunner.io,resources=performancetests,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=marathonrunner.io,resources=performancetests/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=marathonrunner.io,resources=performancetests/finalizers,verbs=update

func (r *PerformanceTestReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Fetch the PerformanceTest instance
	var perfTest marathonrunnerv1alpha1.PerformanceTest
	if err := r.Get(ctx, req.NamespacedName, &perfTest); err != nil {
		if errors.IsNotFound(err) {
			logger.Info("PerformanceTest resource not found, probably deleted")
			return ctrl.Result{}, nil
		}
		logger.Error(err, "Failed to get PerformanceTest")
		return ctrl.Result{}, err
	}

	// Handle deletion
	if !perfTest.ObjectMeta.DeletionTimestamp.IsZero() {
		return r.handleDeletion(ctx, &perfTest)
	}

	// Add finalizer if not present
	if !controllerutil.ContainsFinalizer(&perfTest, performanceTestFinalizer) {
		controllerutil.AddFinalizer(&perfTest, performanceTestFinalizer)
		if err := r.Update(ctx, &perfTest); err != nil {
			return ctrl.Result{}, err
		}
	}

	// Reconcile based on phase
	switch perfTest.Status.Phase {
	case "", marathonrunnerv1alpha1.PerformanceTestPhasePending:
		return r.reconcilePending(ctx, &perfTest)
	case marathonrunnerv1alpha1.PerformanceTestPhaseInProgress:
		return r.reconcileInProgress(ctx, &perfTest)
	case marathonrunnerv1alpha1.PerformanceTestPhaseSucceeded, marathonrunnerv1alpha1.PerformanceTestPhaseFailed:
		return r.reconcileTerminal(ctx, &perfTest)
	default:
		logger.Info("Unknown phase", "phase", perfTest.Status.Phase)
		return ctrl.Result{}, nil
	}
}

func (r *PerformanceTestReconciler) handleDeletion(ctx context.Context, perfTest *marathonrunnerv1alpha1.PerformanceTest) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	if controllerutil.ContainsFinalizer(perfTest, performanceTestFinalizer) {
		// Clean up all TestRun CRs created by this PerformanceTest
		for _, run := range perfTest.Status.Runs {
			if run.RunRef != "" {
				var tr marathonrunnerv1alpha1.TestRun
				err := r.Get(ctx, types.NamespacedName{
					Name:      run.RunRef,
					Namespace: perfTest.Namespace,
				}, &tr)
				if err == nil {
					if err := r.Delete(ctx, &tr); err != nil && !errors.IsNotFound(err) {
						logger.Error(err, "Failed to delete TestRun during cleanup")
						return ctrl.Result{}, err
					}
				}
			}
		}

		controllerutil.RemoveFinalizer(perfTest, performanceTestFinalizer)
		if err := r.Update(ctx, perfTest); err != nil {
			return ctrl.Result{}, err
		}
	}

	return ctrl.Result{}, nil
}

func (r *PerformanceTestReconciler) reconcilePending(ctx context.Context, perfTest *marathonrunnerv1alpha1.PerformanceTest) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	logger.Info("Starting PerformanceTest", "runs", len(perfTest.Spec.Runs))

	// Create TestRun CRs for each run definition
	now := metav1.Now()
	perfTest.Status.Phase = marathonrunnerv1alpha1.PerformanceTestPhaseInProgress
	perfTest.Status.StartTime = &now
	perfTest.Status.ObservedGeneration = perfTest.Generation
	perfTest.Status.Runs = make([]marathonrunnerv1alpha1.PerformanceTestRunStatus, 0, len(perfTest.Spec.Runs))

	for _, runDef := range perfTest.Spec.Runs {
		runName := fmt.Sprintf("%s-%s", perfTest.Name, runDef.Name)

		// Check if TestRun already exists
		var existing marathonrunnerv1alpha1.TestRun
		err := r.Get(ctx, types.NamespacedName{
			Name:      runName,
			Namespace: perfTest.Namespace,
		}, &existing)

		if errors.IsNotFound(err) {
			// Create new TestRun
			testRun := &marathonrunnerv1alpha1.TestRun{
				ObjectMeta: metav1.ObjectMeta{
					Name:      runName,
					Namespace: perfTest.Namespace,
					Labels: map[string]string{
						"marathonrunner.io/performance-test": perfTest.Name,
						"marathonrunner.io/run-name":         runDef.Name,
					},
				},
				Spec: marathonrunnerv1alpha1.TestRunSpec{
					RunID:           int64(len(perfTest.Status.Runs)), // Will be set by API
					Engine:          runDef.Engine,
					TargetEndpoint:  runDef.TargetEndpoint,
					TargetVusers:    runDef.TargetVusers,
					DurationMinutes: runDef.DurationMinutes,
					LoadProfile:     runDef.LoadProfile,
					ScriptConfigMap: runName + "-scripts",
					Namespace:       perfTest.Namespace,
				},
			}

			if err := r.Create(ctx, testRun); err != nil {
				logger.Error(err, "Failed to create TestRun", "name", runName)
				return ctrl.Result{}, err
			}
			logger.Info("Created TestRun", "name", runName)

			perfTest.Status.Runs = append(perfTest.Status.Runs, marathonrunnerv1alpha1.PerformanceTestRunStatus{
				Name:    runDef.Name,
				RunRef:  runName,
				Status:  "Pending",
			})
		} else if err == nil {
			// TestRun already exists, track it
			perfTest.Status.Runs = append(perfTest.Status.Runs, marathonrunnerv1alpha1.PerformanceTestRunStatus{
				Name:    runDef.Name,
				RunRef:  runName,
				Status:  string(existing.Status.Phase),
			})
		} else {
			return ctrl.Result{}, err
		}
	}

	if err := r.Status().Update(ctx, perfTest); err != nil {
		return ctrl.Result{}, err
	}

	return ctrl.Result{}, nil
}

func (r *PerformanceTestReconciler) reconcileInProgress(ctx context.Context, perfTest *marathonrunnerv1alpha1.PerformanceTest) (ctrl.Result, error) {
	allDone := true
	anyFailed := false

	for i, runStatus := range perfTest.Status.Runs {
		if runStatus.RunRef == "" {
			continue
		}

		var testRun marathonrunnerv1alpha1.TestRun
		err := r.Get(ctx, types.NamespacedName{
			Name:      runStatus.RunRef,
			Namespace: perfTest.Namespace,
		}, &testRun)

		if errors.IsNotFound(err) {
			perfTest.Status.Runs[i].Status = "Failed"
			anyFailed = true
			allDone = false
			continue
		}
		if err != nil {
			return ctrl.Result{}, err
		}

		perfTest.Status.Runs[i].Status = string(testRun.Status.Phase)

		switch testRun.Status.Phase {
		case marathonrunnerv1alpha1.TestRunPhaseSucceeded:
			// Done
		case marathonrunnerv1alpha1.TestRunPhaseFailed:
			anyFailed = true
			allDone = false
		default:
			allDone = false
		}
	}

	if allDone {
		now := metav1.Now()
		perfTest.Status.CompletionTime = &now
		if anyFailed {
			perfTest.Status.Phase = marathonrunnerv1alpha1.PerformanceTestPhaseFailed
			perfTest.Status.Message = "One or more runs failed"
		} else {
			perfTest.Status.Phase = marathonrunnerv1alpha1.PerformanceTestPhaseSucceeded
			perfTest.Status.Message = "All runs completed successfully"
		}
	}

	if err := r.Status().Update(ctx, perfTest); err != nil {
		return ctrl.Result{}, err
	}

	if allDone {
		return ctrl.Result{}, nil
	}

	// Requeue to check status
	return ctrl.Result{}, nil
}

func (r *PerformanceTestReconciler) reconcileTerminal(ctx context.Context, perfTest *marathonrunnerv1alpha1.PerformanceTest) (ctrl.Result, error) {
	// Terminal state, no requeue
	return ctrl.Result{}, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *PerformanceTestReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&marathonrunnerv1alpha1.PerformanceTest{}).
		Owns(&marathonrunnerv1alpha1.TestRun{}).
		Complete(r)
}
