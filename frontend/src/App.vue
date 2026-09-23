<template>
  <div class="app">
    <header class="header">
      <h1>OpenShip</h1>
      <p>Agentic DevOps Workflow</p>
      <div class="config-info">
        <span class="config-item">
          <span class="config-label">Cloud:</span>
          {{ config.cloud_provider }} ({{ config.cloud_region }})
        </span>
        <span class="config-item">
          <span class="config-label">LLM:</span>
          {{ config.llm_provider }}/{{ config.llm_model }}
        </span>
      </div>
    </header>

    <div class="workflow">
      <!-- Step indicator -->
      <div class="steps">
        <div
          v-for="step in steps"
          :key="step"
          class="step"
          :class="{
            active: currentStep === step,
            completed: completedSteps.includes(step)
          }"
        >
          {{ step }}
        </div>
      </div>

      <!-- Requirements Step -->
      <div v-if="currentStep === 'Requirements'" class="step-panel">
        <h2>Requirements Document</h2>
        <p>Describe the infrastructure you want to build.</p>
        <md-editor
          v-model="requirements"
          theme="dark"
          preview-theme="dark"
        />
        <button class="btn btn-primary" @click="generateDiagram">
          Generate Diagram
        </button>
      </div>

      <!-- Diagram Step -->
      <div v-if="currentStep === 'Diagram'" class="step-panel">
        <h2>Architecture Diagram</h2>
        <div class="editor-container">
          <div class="editor-pane">
            <h3>Diagram Source</h3>
            <textarea
              v-model="diagramSource"
              class="code-editor"
              spellcheck="false"
            ></textarea>
          </div>
          <div class="preview-pane">
            <h3>Preview</h3>
            <div ref="mermaidPreview" class="mermaid-preview"></div>
          </div>
        </div>
        <div class="step-actions">
          <button class="btn" @click="approveDiagram">Approve Diagram</button>
        </div>
      </div>

      <!-- Terraform Step -->
      <div v-if="currentStep === 'Terraform'" class="step-panel">
        <h2>Terraform Code</h2>
        <div class="editor-container">
          <div class="editor-pane">
            <h3>Terraform Source</h3>
            <textarea
              v-model="terraformSource"
              class="code-editor"
              spellcheck="false"
            ></textarea>
          </div>
        </div>
        <div class="step-actions">
          <button class="btn btn-primary" @click="approveTerraform">
            Approve & Apply
          </button>
        </div>
      </div>

      <!-- Apply Step -->
      <div v-if="currentStep === 'Apply'" class="step-panel">
        <h2>Applying Terraform</h2>
        <div class="output-panel">
          <pre>{{ applyOutput }}</pre>
        </div>
        <div class="step-actions" v-if="workflowComplete">
          <button class="btn btn-primary" @click="resetWorkflow">
            Start New Workflow
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import mermaid from 'mermaid'

// Workflow state
const currentStep = ref('Requirements')
const completedSteps = ref([])
const steps = ['Requirements', 'Diagram', 'Terraform', 'Apply']

// Data
const requirements = ref(`# Web Application Infrastructure

## Requirements
- Web server load-balanced across 2 instances
- PostgreSQL database
- Redis cache
- All resources in us-east-1
- Tags: env=production, app=webapp

## Networking
- Private subnet for database
- Public subnet for web servers
- Security groups for each tier
`)

const diagramSource = ref('')
const terraformSource = ref('')
const applyOutput = ref('')
const workflowComplete = ref(false)

let runId = null
const config = ref({
  cloud_provider: 'aws',
  cloud_region: 'us-east-1',
  llm_provider: 'openai',
  llm_model: 'gpt-4',
  terraform_backend: 'local',
})

async function fetchConfig() {
  try {
    const response = await fetch('/api/config')
    config.value = await response.json()
  } catch (error) {
    console.error('Error fetching config:', error)
  }
}

// Initialize mermaid
onMounted(() => {
  mermaid.initialize({
    theme: 'dark',
    startOnLoad: false,
  })
  fetchConfig()
})

async function generateDiagram() {
  try {
    // Create workflow
    const response = await fetch('/api/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirements: requirements.value }),
    })
    const data = await response.json()
    runId = data.run_id

    // Simulate diagram generation (in PoC, we use a hardcoded diagram)
    diagramSource.value = `graph TD
    A[User] --> B[Load Balancer]
    B --> C[Web Server 1]
    B --> D[Web Server 2]
    C --> E[Database]
    D --> E
    C --> F[Redis Cache]
    D --> F`

    currentStep.value = 'Diagram'
    completedSteps.value.push('Requirements')

    // Render mermaid after step change
    await nextTick()
    renderMermaid()
  } catch (error) {
    console.error('Error generating diagram:', error)
  }
}

async function renderMermaid() {
  const preview = document.querySelector('.mermaid-preview')
  if (!preview || !diagramSource.value) return

  preview.innerHTML = ''
  try {
    const { svg } = await mermaid.render('diagram-' + Date.now(), diagramSource.value)
    preview.innerHTML = svg
  } catch (error) {
    preview.innerHTML = '<div class="error">' + error.message + '</div>'
  }
}

watch(diagramSource, () => {
  renderMermaid()
})

async function approveDiagram() {
  try {
    await fetch(`/api/workflows/${runId}/approve-diagram`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })

    // Simulate Terraform generation
    terraformSource.value = `terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# VPC
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  tags = {
    Name = "openship-vpc"
  }
}

# Load Balancer
resource "aws_lb" "web" {
  name               = "openship-lb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.lb.id]
  subnets            = [aws_subnet.public_1.id, aws_subnet.public_2.id]
}

    # Web Servers
    resource "aws_instance" "web" {
      ami           = "ami-0c55b159cbfafe1f0"
      instance_type = "t2.micro"
      count         = 2
      tags = {
        Name = "openship-web-\${count.index}"
      }
    }

# Database
resource "aws_db_instance" "postgres" {
  identifier     = "openship-db"
  engine         = "postgres"
  engine_version = "14.6"
  instance_class = "db.t3.micro"
  username       = "admin"
  password       = var.db_password
  db_name        = "webapp"
}`

    currentStep.value = 'Terraform'
    completedSteps.value.push('Diagram')
  } catch (error) {
    console.error('Error approving diagram:', error)
  }
}

async function approveTerraform() {
  try {
    await fetch(`/api/workflows/${runId}/approve-terraform`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })

    currentStep.value = 'Apply'
    completedSteps.value.push('Terraform')

    // Simulate apply output
    applyOutput.value = `Terraform used the selected providers to generate the following execution plan.
Resource actions are indicated with the following symbols:
  + create

Terraform will perform the following actions:

  # aws_vpc.main will be created
  + resource "aws_vpc" "main" {
      + cidr_block = "10.0.0.0/16"
      + id         = (known after apply)
      + tags       = {
          + "Name" = "openship-vpc"
        }
    }

  # aws_lb.web will be created
  + resource "aws_lb" "web" {
      + arn                          = (known after apply)
      + dns_name                     = (known after apply)
      + id                           = (known after apply)
      + internal                     = false
      + load_balancer_type           = "application"
      + name                         = "openship-lb"
    }

  # aws_instance.web[0] will be created
  # aws_instance.web[1] will be created
  + 2 (known after apply)

  # aws_db_instance.postgres will be created
  + resource "aws_db_instance" "postgres" {
      + db_name        = "webapp"
      + engine         = "postgres"
      + engine_version = "14.6"
      + identifier     = "openship-db"
      + instance_class = "db.t3.micro"
    }

Plan: 6 to add, 0 to change, 0 to destroy.

aws_vpc.main: Creating...
aws_vpc.main: Creation complete after 2s [id=vpc-0123456789abcdef0]
aws_subnet.public_1: Creating...
aws_subnet.public_2: Creating...
aws_subnet.public_1: Creation complete after 1s [id=subnet-0123456789abcdef0]
aws_subnet.public_2: Creation complete after 1s [id=subnet-0123456789abcdef1]
aws_lb.web: Creating...
aws_lb.web: Creation complete after 8s [id=arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/openship-lb/123456789]
aws_instance.web[0]: Creating...
aws_instance.web[1]: Creating...
aws_instance.web[0]: Creation complete after 12s [id=i-0123456789abcdef0]
aws_instance.web[1]: Creation complete after 12s [id=i-0123456789abcdef1]
aws_db_instance.postgres: Creating...
aws_db_instance.postgres: Creation complete after 45s [id=openship-db]

Apply complete! Resources: 6 added, 0 changed, 0 destroyed.

Outputs:

lb_dns = "openship-lb-123456789.us-east-1.elb.amazonaws.com"
db_endpoint = "openship-db.abcdefgh.us-east-1.rds.amazonaws.com:5432"`

    workflowComplete.value = true
    completedSteps.value.push('Apply')
  } catch (error) {
    console.error('Error applying terraform:', error)
  }
}

function resetWorkflow() {
  currentStep.value = 'Requirements'
  completedSteps.value = []
  diagramSource.value = ''
  terraformSource.value = ''
  applyOutput.value = ''
  workflowComplete.value = false
  runId = null
}
</script>

<style>
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #1a1a1a;
  color: #e0e0e0;
}

.app {
  min-height: 100vh;
  padding: 20px;
}

.header {
  text-align: center;
  margin-bottom: 30px;
}

.header h1 {
  font-size: 2rem;
  color: #4af62c;
}

.header p {
  color: #888;
  margin-top: 5px;
}

.config-info {
  display: flex;
  gap: 16px;
  margin-top: 10px;
  justify-content: center;
}

.config-item {
  font-size: 0.8rem;
  color: #666;
}

.config-label {
  color: #4af62c;
  font-weight: bold;
}

.steps {
  display: flex;
  gap: 10px;
  margin-bottom: 30px;
  justify-content: center;
}

.step {
  padding: 8px 16px;
  background: #2a2a2a;
  border-radius: 20px;
  font-size: 0.9rem;
  color: #666;
}

.step.active {
  background: #4af62c;
  color: #1a1a1a;
  font-weight: bold;
}

.step.completed {
  background: #2d5a27;
  color: #8bc34a;
}

.step-panel {
  max-width: 1200px;
  margin: 0 auto;
  background: #252525;
  border-radius: 12px;
  padding: 24px;
}

.step-panel h2 {
  margin-bottom: 16px;
  color: #fff;
}

.step-panel p {
  margin-bottom: 16px;
  color: #aaa;
}

.editor-container {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 20px;
}

.editor-pane, .preview-pane {
  background: #1e1e1e;
  border-radius: 8px;
  padding: 16px;
}

.editor-pane h3, .preview-pane h3 {
  margin-bottom: 12px;
  font-size: 0.9rem;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.code-editor {
  width: 100%;
  min-height: 400px;
  background: transparent;
  border: none;
  color: #e0e0e0;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 14px;
  line-height: 1.5;
  resize: vertical;
  outline: none;
}

.mermaid-preview {
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.output-panel {
  background: #1e1e1e;
  border-radius: 8px;
  padding: 16px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.4;
  max-height: 500px;
  overflow-y: auto;
  white-space: pre-wrap;
}

.step-actions {
  display: flex;
  gap: 12px;
  margin-top: 20px;
  justify-content: flex-end;
}

.btn {
  padding: 10px 24px;
  border: none;
  border-radius: 6px;
  font-size: 1rem;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-primary {
  background: #4af62c;
  color: #1a1a1a;
  font-weight: bold;
}

.btn-primary:hover {
  background: #5df842;
}

.btn {
  background: #333;
  color: #e0e0e0;
}

.btn:hover {
  background: #444;
}
</style>