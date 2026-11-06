package com.iwacollection.cicd

import groovy.json.JsonSlurperClassic
import java.util.regex.Pattern

/**
 * Validation helpers shared by goToolchainBuild and cppToolchainBuild.
 *
 * Repository-slug and relative-path rules are delegated to JavaToolchainSpec so
 * there is exactly one implementation of them.
 */
class NativeToolchainSpec implements Serializable {
    private static final long serialVersionUID = 1L
    private static final Pattern ID_PATTERN = ~/^[a-z0-9][a-z0-9._-]{0,127}$/
    private static final Pattern IMAGE_PATTERN = ~/^.+@sha256:[0-9a-f]{64}$/
    private static final Pattern GO_ID_PATTERN = ~/^go[0-9][a-z0-9._-]*$/
    private static final Pattern CPP_ID_PATTERN = ~/^(gcc|clang)[a-z0-9._-]*$/

    static String requireLanguage(String language) {
        String value = language == null ? '' : language.trim()
        if (value != 'go' && value != 'cpp') {
            throw new IllegalArgumentException("language must be 'go' or 'cpp', got '${value}'")
        }
        return value
    }

    /** A Go step may only consume Go toolchains, a C++ step only gcc/clang ones. */
    static boolean toolchainIdMatchesLanguage(String id, String language) {
        String lang = requireLanguage(language)
        return lang == 'go' ? (id ==~ GO_ID_PATTERN) : (id ==~ CPP_ID_PATTERN)
    }

    static Map parseProjectConfig(String content, String language) {
        String expectedLanguage = requireLanguage(language)
        if (content == null || !content.trim()) {
            throw new IllegalArgumentException("${expectedLanguage} toolchain project config is empty")
        }

        Object parsed
        try {
            parsed = new JsonSlurperClassic().parseText(content)
        } catch (Exception ex) {
            throw new IllegalArgumentException("${expectedLanguage} toolchain project config is not valid JSON", ex)
        }

        if (!(parsed instanceof Map)) {
            throw new IllegalArgumentException('toolchain project config root must be an object')
        }

        Map config = (Map) parsed
        if (config.schema_version != 1) {
            throw new IllegalArgumentException('toolchain project config schema_version must be 1')
        }
        if (config.containsKey('language') && config.language != expectedLanguage) {
            throw new IllegalArgumentException(
                "project config language '${config.language}' does not match the ${expectedLanguage} build step"
            )
        }

        String toolchain = config.toolchain instanceof String ? config.toolchain.trim() : ''
        if (!toolchain || !(toolchain ==~ ID_PATTERN)) {
            throw new IllegalArgumentException('toolchain must be a non-empty lowercase Toolchain ID')
        }
        if (!toolchainIdMatchesLanguage(toolchain, expectedLanguage)) {
            throw new IllegalArgumentException("toolchain '${toolchain}' is not a ${expectedLanguage} toolchain")
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

        List reports = config.test_report_globs ?: []
        if (!(reports instanceof List) || reports.any { !(it instanceof String) || !it.trim() }) {
            throw new IllegalArgumentException('test_report_globs must be a string array')
        }
        reports.each { validateRelativePath(it.toString(), 'test_report_globs entry') }

        ['test_command', 'verify_command', 'project_name'].each { String key ->
            if (config.containsKey(key) && !(config[key] instanceof String)) {
                throw new IllegalArgumentException("${key} must be a string when provided")
            }
        }

        String testCommand = config.containsKey('test_command') ? config.test_command.trim() : ''
        String verifyCommand = config.containsKey('verify_command') ? config.verify_command.trim() : ''
        String projectName = config.containsKey('project_name') ? config.project_name.trim() : toolchain
        if (!projectName) {
            throw new IllegalArgumentException('project_name must not be empty')
        }

        return [
            schema_version: 1,
            language: expectedLanguage,
            project_name: projectName,
            toolchain: toolchain,
            build_command: buildCommand,
            test_command: testCommand,
            verify_command: verifyCommand,
            artifact_globs: artifacts.collect { it.toString() },
            dependency_lock_files: locks.collect { it.toString() },
            test_report_globs: reports.collect { it.toString() }
        ]
    }

    static Map validateResolvedToolchain(Object value, String language) {
        String expectedLanguage = requireLanguage(language)
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException('Resolved toolchain response must be a JSON object')
        }

        Map resolved = (Map) value
        String id = resolved.id instanceof String ? resolved.id : ''
        if (resolved.status != 'active') {
            throw new IllegalArgumentException(
                "Toolchain ${id ?: '<unknown>'} is not Active; current status is ${resolved.status ?: 'unknown'}"
            )
        }
        if (resolved.execution_mode != 'container') {
            throw new IllegalArgumentException("${expectedLanguage} project toolchain must use container execution mode")
        }

        String image = resolved.image instanceof String ? resolved.image : ''
        if (!(image ==~ IMAGE_PATTERN)) {
            throw new IllegalArgumentException("Resolved ${expectedLanguage} toolchain image must be pinned by @sha256 digest")
        }

        String identity = resolved.identity instanceof String ? resolved.identity : ''
        String expectedDigest = image.substring(image.lastIndexOf('@') + 1)
        if (identity != expectedDigest) {
            throw new IllegalArgumentException('Resolved toolchain identity does not match image digest')
        }

        if (!id || !(id ==~ ID_PATTERN)) {
            throw new IllegalArgumentException('Resolved toolchain ID is invalid')
        }
        if (!toolchainIdMatchesLanguage(id, expectedLanguage)) {
            throw new IllegalArgumentException("Resolved toolchain '${id}' is not a ${expectedLanguage} toolchain")
        }
        return [
            id: id,
            status: resolved.status,
            execution_mode: resolved.execution_mode,
            image: image,
            identity: identity
        ]
    }

    static String normalizeRepositorySlug(String gitUrl) {
        return JavaToolchainSpec.normalizeRepositorySlug(gitUrl)
    }

    static String validateRelativePath(String value, String fieldName) {
        return JavaToolchainSpec.validateRelativePath(value, fieldName)
    }
}
