data "aws_caller_identity" "atual" {}

data "aws_cloudfront_cache_policy" "otimizada" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "desligada" {
  name = "Managed-CachingDisabled"
}

# Cabecalhos de seguranca do site e da API (Front-End-Checklist). Substitui o
# Managed-SecurityHeadersPolicy, que nao tem CSP nem Permissions-Policy.
# A CSP segue o que o build carrega: JS, CSS e fontes do proprio site, a API no
# mesmo dominio (/api) e o login direto na API JSON do Cognito da regiao
# (src/dados/sessao.ts). style-src leva 'unsafe-inline' porque React e Astryx
# poem atributos style em tempo de execucao (alturas do Skeleton, tema).
locals {
  csp_site = join("; ", [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self' https://cognito-idp.${var.regiao}.amazonaws.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "upgrade-insecure-requests",
  ])
}

resource "aws_cloudfront_response_headers_policy" "seguranca" {
  name    = "radar-seguranca"
  comment = "HSTS, CSP, nosniff, frame DENY, referrer e permissions do radar"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      override                   = true
    }

    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    content_security_policy {
      content_security_policy = local.csp_site
      override                = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
      override = true
    }
  }

  # Nao conta ao visitante que a origem e S3 nem como o bucket cifra.
  remove_headers_config {
    items {
      header = "Server"
    }
    items {
      header = "X-Amz-Server-Side-Encryption"
    }
  }
}

data "aws_cloudfront_origin_request_policy" "todos_menos_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_s3_bucket" "front" {
  bucket = "radar-licitacoes-front-${data.aws_caller_identity.atual.account_id}"
}

resource "aws_s3_bucket_ownership_controls" "front" {
  bucket = aws_s3_bucket.front.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "front" {
  bucket                  = aws_s3_bucket.front.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "front" {
  bucket = aws_s3_bucket.front.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "front" {
  name                              = "radar-front"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "radar-licitacoes"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    origin_id                = "s3-front"
    domain_name              = aws_s3_bucket.front.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.front.id
  }

  origin {
    origin_id   = "api-gateway"
    domain_name = "${aws_apigatewayv2_api.api.id}.execute-api.${var.regiao}.amazonaws.com"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-front"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.otimizada.id

    response_headers_policy_id = aws_cloudfront_response_headers_policy.seguranca.id
    compress                   = true
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "api-gateway"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.desligada.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.todos_menos_host.id
    compress                 = true

    # Mesma politica na API: o JSON tambem ganha nosniff, HSTS e frame DENY.
    response_headers_policy_id = aws_cloudfront_response_headers_policy.seguranca.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "front_bucket" {
  statement {
    sid       = "SomenteCloudFront"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.front.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "front" {
  bucket     = aws_s3_bucket.front.id
  policy     = data.aws_iam_policy_document.front_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.front]
}
