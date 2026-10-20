<template>
  <div class="h-full flex flex-col">
    <!-- Header -->
    <div class="border-b border-[#30363d] px-6 py-4 bg-[#161b22]">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <router-link to="/agent-workspace" class="text-sm text-[#58a6ff] hover:text-[#79b8ff]">
            ← Back to chat
          </router-link>
          <span class="text-sm text-gray-400">/</span>
          <h1 class="text-lg font-semibold text-gray-100">main.tf</h1>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">synced</span>
          <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
            Commit
          </button>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="flex border-b border-[#30363d] bg-[#161b22]">
      <button v-for="tab in ['code', 'diagram', 'terminal']" :key="tab"
        @click="activeTab = tab"
        :class="{'border-[#58a6ff] text-[#58a6ff] bg-[#0d1117]': activeTab === tab, 'border-transparent text-gray-400 hover:text-gray-200 hover:bg-[#0d1117]/50': activeTab !== tab}"
        class="px-4 py-2 text-sm font-medium border-b-2 transition-colors capitalize">
        {{ tab }}
      </button>
    </div>

    <!-- Code Tab -->
    <div v-if="activeTab === 'code'" class="flex-1 overflow-auto bg-[#0d1117]">
      <div class="flex">
        <div class="w-12 bg-[#161b22] text-right pr-2 py-4 text-xs text-gray-500 font-mono border-r border-[#30363d]">
          <div v-for="n in 50" :key="n">{{ n }}</div>
        </div>
        <div class="flex-1 py-4 px-4 font-mono text-xs text-gray-300">
<pre><span class="text-purple-400">resource</span> <span class="text-yellow-300">"aws_alb"</span> <span class="text-yellow-300">"web"</span> {
  name               = <span class="text-green-400">"web-platform-alb"</span>
  internal           = <span class="text-orange-400">false</span>
  load_balancer_type = <span class="text-green-400">"application"</span>
  subnets            = module.vpc.public_subnets

  tags = {
    Name = <span class="text-green-400">"web-platform-alb"</span>
  }
}

<span class="text-purple-400">resource</span> <span class="text-yellow-300">"aws_alb_target_group"</span> <span class="text-yellow-300">"api"</span> {
  name     = <span class="text-green-400">"api-tg"</span>
  port     = <span class="text-orange-400">8080</span>
  protocol = <span class="text-green-400">"HTTP"</span>
  vpc_id   = module.vpc.vpc_id

  health_check {
    path                = <span class="text-green-400">"/health"</span>
    healthy_threshold   = <span class="text-orange-400">2</span>
    unhealthy_threshold = <span class="text-orange-400">3</span>
    timeout             = <span class="text-orange-400">5</span>
    interval            = <span class="text-orange-400">30</span>
  }
}

<span class="text-purple-400">resource</span> <span class="text-yellow-300">"aws_autoscaling_group"</span> <span class="text-yellow-300">"api"</span> {
  name                 = <span class="text-green-400">"api-asg"</span>
  min_size             = <span class="text-orange-400">2</span>
  max_size             = <span class="text-orange-400">6</span>
  desired_capacity     = <span class="text-orange-400">3</span>
  vpc_zone_identifier  = module.vpc.private_subnets
  target_group_arns    = [aws_alb_target_group.api.arn]

  launch_template {
    id      = aws_launch_template.api.id
    version = <span class="text-green-400">"$Default"</span>
  }

  tag {
    key                 = <span class="text-green-400">"Name"</span>
    value               = <span class="text-green-400">"api-server"</span>
    propagate_at_launch = <span class="text-orange-400">true</span>
  }
}</pre>
        </div>
      </div>
    </div>

    <!-- Diagram Tab -->
    <div v-else-if="activeTab === 'diagram'" class="flex-1 overflow-auto bg-[#0d1117] p-6">
      <div class="flex items-center justify-center h-full">
        <div class="text-center">
          <pre class="text-xs text-gray-400 font-mono">
     [Internet]
        |
     [ALB: web-platform-alb]
     /   |   \
[API-1] [API-2] [API-3]
  \     |     /
    [PostgreSQL: rds-cluster]
    [Redis: elasticache-cluster]</pre>
          <p class="text-xs text-gray-500 mt-4">Live preview • Last updated: just now</p>
        </div>
      </div>
    </div>

    <!-- Terminal Tab -->
    <div v-else-if="activeTab === 'terminal'" class="flex-1 overflow-auto bg-[#0d1117] p-4 font-mono text-xs">
      <div class="space-y-1 text-gray-300">
        <p><span class="text-green-400">✔</span> terraform init</p>
        <p><span class="text-green-400">✔</span> terraform validate</p>
        <p><span class="text-blue-400">▶</span> terraform apply -auto-approve</p>
        <p class="text-gray-400">aws_vpc.main: Creating...</p>
        <p class="text-green-400">aws_vpc.main: Creation complete after 2s [id=vpc-123abc]</p>
        <p class="text-gray-400">aws_alb.web: Creating...</p>
        <p class="text-green-400">aws_alb.web: Creation complete after 15s [id=arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/web-platform-alb/abc123]</p>
        <p class="text-yellow-400">Applying... (5 resources to add)</p>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  data() {
    return {
      activeTab: 'code',
    }
  }
}
</script>