IMG ?= marathonrunner-operator:latest

.PHONY: all
all: build

##@ General

.PHONY: help
help:
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

.PHONY: fmt
fmt:
	go fmt ./...

.PHONY: vet
vet:
	go vet ./...

.PHONY: test
test:
	go test ./... -coverprofile cover.out

##@ Build

.PHONY: build
build: fmt vet
	go build -o bin/manager main.go

.PHONY: run
run: fmt vet
	go run main.go

.PHONY: docker-build
docker-build:
	docker build -t ${IMG} .

.PHONY: docker-push
docker-push:
	docker push ${IMG}

##@ Deployment

.PHONY: install
install:
	kubectl apply -f config/crd/bases/

.PHONY: uninstall
uninstall:
	kubectl delete -f config/crd/bases/

.PHONY: deploy
deploy:
	kubectl apply -f config/rbac/
	kubectl apply -f config/manager/

.PHONY: undeploy
undeploy:
	kubectl delete -f config/manager/
	kubectl delete -f config/rbac/
