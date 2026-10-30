package com.iwacollection.cicd

import groovy.json.JsonSlurperClassic
import java.util.regex.Pattern

/**
 * Pure validation helpers shared by Jenkins Pipeline steps.
 *
 * Keep this class free of Jenkins APIs so it can be unit-tested outside a
 * controller and so registry policy remains in scripts/ci/resolve_toolchain.py.
 */
class JavaToolchainSpec implements Serializable {
    private static final long serialVersionUID = 1L
    private static final Pattern ID_PATTERN = ~/^[a-z0-9][a-z0-9._-]{0,127}$/
    private static final Pattern IMAGE_PATTERN = ~/^.+@sha256:[0-9a-f]{64}$/

    static Map parseProjectConfig(String content) {
        if (content == null || !content.trim()) {
            throw new IllegalArgumentException('Java toolchain project config is empty')
        }

        Object parsed
        try {
            parsed = new JsonSlurperClassic().parseText(content)
        } catch (Exception ex) {
            throw new IllegalArgumentException('Java toolchain project config is not valid JSON', ex)
        }

        if (!(parsed instanceof Map)) {
            throw new IllegalArgumentException('Java toolchain project config root must be an object')
        }

        Map config = (Map) parsed
        if (config.schema_version != 1) {
            throw new IllegalArgumentException('Java toolchain project config schema_version must be 1')
        }

        String toolchain = config.toolchain instanceof String ? config.toolchain.trim() : ''
        if (!toolchain || !(toolchain ==~ ID_PATTERN)) {
            throw new IllegalArgumentException('toolchain must be a non-empty lowercase Toolchain ID')
        }

        String buildCommand = config.build_command instanceof String ? config.build_command.trim() : ''
        if (!buildCommand) {
            throw new IllegalArgumentException('build_command must be a non-empty string')
        }

        List artifacts = config.artifact_globs
        if (!(artifacts instanceof List) || artifacts.isEmpty()
            || artifacts.any { !(it instanceof String) || !it.trim() }) {
            throw new IllegalArgumentException('artifact_globs must be a non-empty string array')
        }
        artifacts.each { validateRelativePath(it.toString(), 'artifact_globs entry') }

        List locks = config.dependency_lock_files ?: []
        if (!(locks instanceof List) || locks.any { !(it instanceof String) || !it.trim() }) {
            throw new IllegalArgumentException('dependency_lock_files must be a string array')
        }
        locks.each { validateRelativePath(it.toString(), 'dependency_lock_files entry') }

        if (config.containsKey('test_command') && !(config.test_command instanceof String)) {
            throw new IllegalArgumentException('test_command must be a string when provided')
        }
        if (config.containsKey('verify_command') && !(config.verify_command instanceof String)) {
            throw new IllegalArgumentException('verify_command must be a string when provided')
        }
        if (config.containsKey('project_name') && !(config.project_name instanceof String)) {
            throw new IllegalArgumentException('project_name must be a string when provided')
        }

        String testCommand = config.containsKey('test_command') ? config.test_command.trim() : ''
        String verifyCommand = config.containsKey('verify_command') ? config.verify_command.trim() : ''
        String projectName = config.containsKey('project_name') ? config.project_name.trim() : toolchain
        if (!projectName) {
            throw new IllegalArgumentException('project_name must not be empty')
        }

        return [
            schema_version: 1,
            project_name: projectName,
            toolchain: toolchain,
            build_command: buildCommand,
            test_command: testCommand,
            verify_command: verifyCommand,
            artifact_globs: artifacts.collect { it.toString() },
            dependency_lock_files: locks.collect { it.toString() }
        ]
    }

    static Map validateResolvedToolchain(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException('Resolved toolchain response must be a JSON object')
        }

        Map resolved = (Map) value
        if (resolved.status != 'active') {
            throw new IllegalArgumentException(
                "Toolchain ${resolved.id ?: '<unknown>'} is not Active; current status is ${resolved.status ?: 'unknown'}"
            )
        }
        if (resolved.execution_mode != 'container') {
            throw new IllegalArgumentException('Java project toolchain must use container execution mode')
        }

        String image = resolved.image instanceof String ? resolved.image : ''
        if (!(image ==~ IMAGE_PATTERN)) {
            throw new IllegalArgumentException('Resolved Java toolchain image must be pinned by @sha256 digest')
        }

        String identity = resolved.identity instanceof String ? resolved.identity : ''
        String expectedDigest = image.substring(image.lastIndexOf('@') + 1)
        if (identity != expectedDigest) {
            throw new IllegalArgumentException('Resolved toolchain identity does not match image digest')
        }

        String id = resolved.id instanceof String ? resolved.id : ''
        if (!id || !(id ==~ ID_PATTERN)) {
            throw new IllegalArgumentException('Resolved toolchain ID is invalid')
        }
        return [
            id: id,
            status: resolved.status,
            execution_mode: resolved.execution_mode,
            image: image,
            identity: identity,
            jdk_vendor: resolved.jdk_vendor ?: '',
            jdk_version: resolved.jdk_version ?: '',
            maven_version: resolved.maven_version ?: ''
        ]
    }

    static String normalizeRepositorySlug(String gitUrl) {
        String value = gitUrl == null ? '' : gitUrl.trim()
        if (!value) {
            throw new IllegalArgumentException('GIT_URL must not be empty')
        }

        String path = ''
        try {
            if (value ==~ /^[^@\\/]+@[^:]+:.+$/) {
                // SCP-style SSH URL: git@github.com:owner/repo.git
                path = value.substring(value.indexOf(':') + 1)
            } else {
                java.net.URI uri = new java.net.URI(value)
                if (!uri.host) {
                    throw new IllegalArgumentException('GIT_URL must include a repository host')
                }
                path = uri.path ?: ''
            }
        } catch (java.net.URISyntaxException ex) {
            throw new IllegalArgumentException('GIT_URL is not a valid repository URL', ex)
        }

        path = path.replaceAll('^/+', '').replaceAll('/+
        String path = value == null ? '' : value.trim().replace('\\', '/')
        if (!path || path.startsWith('/') || path ==~ /^[A-Za-z]:.*/) {
            throw new IllegalArgumentException("${fieldName} must be a non-empty relative path")
        }
        List parts = path.split('/') as List
        if (parts.any { it == '..' || it == '' }) {
            throw new IllegalArgumentException("${fieldName} contains an unsafe path segment")
        }
        return path
    }
}
, '')
        if (path.toLowerCase().endsWith('.git')) {
            path = path.substring(0, path.length() - 4)
        }
        List<String> segments = path.split('/') as List<String>
        if (segments.size() != 2 || segments.any { !it || it == '.' || it == '..' }) {
            throw new IllegalArgumentException('GIT_URL must resolve to exactly owner/repository')
        }
        return segments[0] + '/' + segments[1]
    }

    static String validateRelativePath(String value, String fieldName) {
        String path = value == null ? '' : value.trim().replace('\\', '/')
        if (!path || path.startsWith('/') || path ==~ /^[A-Za-z]:.*/) {
            throw new IllegalArgumentException("${fieldName} must be a non-empty relative path")
        }
        List parts = path.split('/') as List
        if (parts.any { it == '..' || it == '' }) {
            throw new IllegalArgumentException("${fieldName} contains an unsafe path segment")
        }
        return path
    }
}
