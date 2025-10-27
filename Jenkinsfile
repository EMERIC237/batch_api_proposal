
pipeline {
  agent any
  options { timestamps(); disableConcurrentBuilds() }

  parameters {
    string(name: 'RELEASE_VERSION', defaultValue: '', description: 'Override version for release/* if needed.')
    booleanParam(name: 'DOCKER_BUILD', defaultValue: true, description: 'If true, build a container on release/*; else use ARTIFACT_PATH checksum.')
    string(name: 'ARTIFACT_PATH', defaultValue: 'build/libs/app.jar', description: 'Used when DOCKER_BUILD=false.')
    choice(name: 'DEPLOY_TOOL', choices: ['helm','kustomize','script'], description: 'Deployment method.')
    booleanParam(name: 'FF_MASTER', defaultValue: false, description: 'Fast-forward master from release tip after tagging.')
  }

  environment {
    IMAGE_NAME        = 'ghcr.io/ORG/APP'
    GIT_PUSH_CREDS_ID = 'git-push-creds'
  }

  stages {
    stage('Init') {
      steps {
        script {
          env.IS_RELEASE = env.BRANCH_NAME?.startsWith('release/') ? 'true' : 'false'
          env.IS_DEVELOP = (env.BRANCH_NAME == 'develop') ? 'true' : 'false'
          env.IS_FEATURE = env.BRANCH_NAME?.startsWith('feature/') ? 'true' : 'false'
          env.IS_HOTFIX  = env.BRANCH_NAME?.startsWith('hotfix/')  ? 'true' : 'false'
        }
      }
    }

    stage('Checkout') {
      steps {
        checkout scm
        sh 'git fetch --tags --force || true'
        script {
          env.SHORT_SHA = sh(returnStdout: true, script: 'git rev-parse --short=8 HEAD').trim()
          env.VERSION = (env.IS_RELEASE == 'true') ? (params.RELEASE_VERSION?.trim() ? params.RELEASE_VERSION.trim() : env.BRANCH_NAME.replace("release/","")) : env.SHORT_SHA
          echo "Branch=${env.BRANCH_NAME}, VERSION=${env.VERSION}"
        }
      }
    }

    stage('Quality Gates') {
      when { anyOf {
        expression { env.IS_DEVELOP == 'true' }
        expression { env.IS_FEATURE == 'true' }
        expression { env.IS_HOTFIX  == 'true' }
        expression { env.IS_RELEASE == 'true' }
      }}
      steps { sh 'echo "Run tests, lint, scans, TF plan..."' }
    }

    stage('Build (non-release)') {
      when { anyOf { expression { env.IS_DEVELOP == 'true' }; expression { env.IS_FEATURE == 'true' }; expression { env.IS_HOTFIX == 'true' } } }
      steps { sh 'echo "Build for DEV/testing only (not used for PROD promotion)."' }
    }

    stage('Build ONCE (release/*)') {
      when { expression { env.IS_RELEASE == 'true' } }
      steps {
        sh '''
set -e
if [ "${DOCKER_BUILD}" = "true" ]; then
  IMAGE_REF="${IMAGE_NAME}:${VERSION}-${GIT_COMMIT:0:8}"
  printf "version: %s\ncommit: %s\nbuilt_at: %s\nimage_ref: %s\n"     "${VERSION}" "$(git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${IMAGE_REF}" > release-manifest.yaml
else
  if [ ! -f "${ARTIFACT_PATH}" ]; then echo "artifact not found"; exit 1; fi
  SHA256=$(sha256sum "${ARTIFACT_PATH}" | awk '{print $1}')
  printf "version: %s\ncommit: %s\nbuilt_at: %s\nartifact_path: %s\nartifact_sha256: %s\n"     "${VERSION}" "$(git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${ARTIFACT_PATH}" "${SHA256}" > release-manifest.yaml
fi
cat release-manifest.yaml
'''
        archiveArtifacts artifacts: 'release-manifest.yaml', fingerprint: true
      }
    }

    stage('Deploy DEV (develop)') {
      when { expression { env.IS_DEVELOP == 'true' } }
      steps { sh 'echo "Deploy to DEV (develop)"' }
    }

    stage('Deploy UAT (release/*)') {
      when { expression { env.IS_RELEASE == 'true' } }
      steps { sh 'echo "Deploy to UAT using release-manifest.yaml (same artifact)"' }
    }

    stage('Approve Prod Promotion') {
      when { expression { env.IS_RELEASE == 'true' } }
      steps { script { input message: "Approve promotion of SAME artifact to PROD?", ok: "Promote" } }
    }

    stage('Promote PROD (same artifact)') {
      when { expression { env.IS_RELEASE == 'true' } }
      steps { sh 'echo "Promote to PROD using the SAME digest/checksum from release-manifest.yaml"' }
    }

    stage('Tag Release (vX.Y.Z)') {
      when { expression { env.IS_RELEASE == 'true' } }
      steps {
        withCredentials([usernamePassword(credentialsId: env.GIT_PUSH_CREDS_ID, passwordVariable: 'GIT_PASS', usernameVariable: 'GIT_USER')]) {
          sh '''
set -e
VERSION_LINE=$(awk -F': ' '/^version:/{print $2}' release-manifest.yaml)
VERSION="${VERSION_LINE:-${VERSION}}"
if [ -z "${VERSION}" ]; then echo "version not found"; exit 1; fi
git config user.name  "release-bot"
git config user.email "release-bot@company.com"
TARGET=$(git rev-parse HEAD)
git tag -a "v${VERSION}" "${TARGET}" -m "Release ${VERSION}"
ORIGIN=$(git remote get-url origin | sed "s#https://#https://${GIT_USER}:${GIT_PASS}@#")
git push "${ORIGIN}" "v${VERSION}"
'''
        }
      }
    }

    stage('Fast-forward master (optional)') {
      when { allOf { expression { env.IS_RELEASE == 'true' }; expression { params.FF_MASTER } } }
      steps {
        withCredentials([usernamePassword(credentialsId: env.GIT_PUSH_CREDS_ID, passwordVariable: 'GIT_PASS', usernameVariable: 'GIT_USER')]) {
          sh '''
set -e
git fetch origin master
git branch -f master HEAD
ORIGIN=$(git remote get-url origin | sed "s#https://#https://${GIT_USER}:${GIT_PASS}@#")
git push "${ORIGIN}" master
'''
        }
      }
    }
  }

  post {
    always { archiveArtifacts artifacts: 'release-manifest.yaml', allowEmptyArchive: true, fingerprint: true }
    failure { echo 'Pipeline failed.' }
  }
}
