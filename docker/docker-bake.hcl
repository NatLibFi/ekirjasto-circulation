# Build all runtime targets together so BuildKit shares the common stage.
variable "BASE_IMAGE" {
  default = "ghcr.io/natlibfi/ekirjasto-circ-baseimage:latest"
}

variable "OS_REFRESH" {
  default = "local"
}

variable "IMAGE_VERSION" {
  default = "local"
}

variable "SOURCE_REVISION" {
  default = ""
}

variable "SOURCE_URL" {
  default = ""
}

group "default" {
  targets = ["webapp", "scripts", "exec"]
}

target "common" {
  context = "."
  dockerfile = "docker/Dockerfile"
  args = { BASE_IMAGE = BASE_IMAGE }
  labels = {
    "org.opencontainers.image.version" = IMAGE_VERSION
    "org.opencontainers.image.revision" = SOURCE_REVISION
    "org.opencontainers.image.source" = SOURCE_URL
  }
  # CI builds the base and runtime images together, sharing cached layers.
  contexts = BASE_IMAGE == "baseimage" ? { baseimage = "target:baseimage" } : {}
}

target "baseimage" {
  context = "."
  dockerfile = "docker/Dockerfile.baseimage"
  target = "baseimage"
  args = { OS_REFRESH = OS_REFRESH }
}

target "webapp" {
  inherits = ["common"]
  target = "webapp"
  tags = ["circulation-ci-webapp:local"]
}

target "scripts" {
  inherits = ["common"]
  target = "scripts"
  tags = ["circulation-ci-scripts:local"]
}

target "exec" {
  inherits = ["common"]
  target = "exec"
  tags = ["circulation-ci-exec:local"]
}
