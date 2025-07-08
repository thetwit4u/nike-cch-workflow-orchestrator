#!groovy
@Library('cop-pipeline-bootstrap') _ // Load the a bunch of standard Nike libs

loadPipelines('trade', 'trade') // Load the main pipeline

def parameterMap = [
  name: "cch-workflow-orchestrator",
  description: "The project contains all code & infra configuration (that isn't in the general infrastructure-repo) specific for cch workflow orchestrator.",
  buildSubPath: "workflow-orchestrator/src",
  deploySubPath: "workflow-orchestrator/cdk",
  env: env
]

def config = [
  profile: [
    team: "team/trade/import-export/common.groovy",
    quality: "team/trade/import-export/quality/python.groovy",
    build: "team/trade/import-export/build/python-container.groovy",
    deploy: "team/trade/import-export/deploy/cdk.groovy"
  ]
]

node {
    config = mergeConfiguration(config, parameterMap)
}

tradePipeline(config)