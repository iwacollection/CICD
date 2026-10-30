package com.iwacollection.cicd.tests;

import groovy.lang.Binding;
import groovy.lang.GroovyClassLoader;
import groovy.lang.Script;
import org.junit.Test;

import java.io.File;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.Assert.*;

public class JavaToolchainSharedLibraryTest {
    private static final String PLATFORM_SHA = "eadbbda40f2458935b3413e19bed56e0d61c12aa";
    private static final String DIGEST = "sha256:" + "a".repeat(64);
    private static final String IMAGE =
            "ghcr.io/iwacollection/cicd-toolchain-java17-maven@" + DIGEST;

    private static final String PROJECT_CONFIG = """
            {
              "schema_version": 1,
              "project_name": "java17-maven-service",
              "toolchain": "java17-maven",
              "build_command": "mvn -B -ntp clean verify",
              "test_command": "",
              "verify_command": "java -jar target/java17-maven-service-1.0.0.jar",
              "artifact_globs": ["target/java17-maven-service-1.0.0.jar"],
              "dependency_lock_files": ["pom.xml"]
            }
            """;

    private static final String ACTIVE_RESOLUTION = """
            {
              "id": "java17-maven",
              "status": "active",
              "execution_mode": "container",
              "image": "%s",
              "identity": "%s",
              "jdk_vendor": "Eclipse Temurin",
              "jdk_version": "17.0.21+9",
              "maven_version": "3.10.0"
            }
            """.formatted(IMAGE, DIGEST);

    private GroovyClassLoader newLoader() throws Exception {
        Path root = Paths.get("..").toAbsolutePath().normalize();
        GroovyClassLoader loader = new GroovyClassLoader(getClass().getClassLoader());
        loader.addClasspath(root.resolve("src").toString());
        loader.parseClass(root.resolve("src/com/iwacollection/cicd/JavaToolchainSpec.groovy").toFile());
        return loader;
    }

    @Test
    public void projectDeclarationAcceptsToolchainIdWithoutImageOverride() throws Exception {
        GroovyClassLoader loader = newLoader();
        Class<?> spec = loader.loadClass("com.iwacollection.cicd.JavaToolchainSpec");
        Method parse = spec.getMethod("parseProjectConfig", String.class);
        Map<?, ?> result = (Map<?, ?>) parse.invoke(null, PROJECT_CONFIG);

        assertEquals("java17-maven", result.get("toolchain"));
        assertEquals("java17-maven-service", result.get("project_name"));
        assertEquals(List.of("pom.xml"), result.get("dependency_lock_files"));
    }

    @Test
    public void candidateIsRejectedBySharedPolicyHelper() throws Exception {
        GroovyClassLoader loader = newLoader();
        Class<?> spec = loader.loadClass("com.iwacollection.cicd.JavaToolchainSpec");
        Method validate = spec.getMethod("validateResolvedToolchain", Object.class);

        Map<String, Object> candidate = new LinkedHashMap<>();
        candidate.put("id", "java17-maven");
        candidate.put("status", "candidate");
        candidate.put("execution_mode", "container");
        candidate.put("image", IMAGE);
        candidate.put("identity", DIGEST);

        try {
            validate.invoke(null, candidate);
            fail("Candidate must not be consumable by the production Shared Library");
        } catch (InvocationTargetException ex) {
            assertTrue(ex.getCause() instanceof IllegalArgumentException);
            assertTrue(ex.getCause().getMessage().contains("not Active"));
        }
    }

    @Test
    public void activeDigestIsValidatedAndReturned() throws Exception {
        GroovyClassLoader loader = newLoader();
        Class<?> spec = loader.loadClass("com.iwacollection.cicd.JavaToolchainSpec");
        Method validate = spec.getMethod("validateResolvedToolchain", Object.class);
        Object parsed = new groovy.json.JsonSlurperClassic().parseText(ACTIVE_RESOLUTION);
        Map<?, ?> result = (Map<?, ?>) validate.invoke(null, parsed);

        assertEquals("active", result.get("status"));
        assertEquals(IMAGE, result.get("image"));
        assertEquals(DIGEST, result.get("identity"));
    }

    @Test
    public void sharedLibraryScriptCompilesAgainstJenkinsGroovyRuntime() throws Exception {
        GroovyClassLoader loader = newLoader();
        Path vars = Paths.get("../vars/javaToolchainBuild.groovy").toAbsolutePath().normalize();
        Class<?> scriptClass = loader.parseClass(vars.toFile());
        assertNotNull(scriptClass);
    }

    @Test
    public void fullSharedLibraryCallUsesPinnedResolverSignaturePullBuildAndArtifactContract() throws Exception {
        GroovyClassLoader loader = newLoader();
        Path vars = Paths.get("../vars/javaToolchainBuild.groovy").toAbsolutePath().normalize();
        Class<?> scriptClass = loader.parseClass(vars.toFile());
        Script script = (Script) scriptClass.getDeclaredConstructor().newInstance();

        Map<String, Object> env = new HashMap<>();
        env.put("BRANCH_NAME", "main");
        env.put("GIT_BRANCH", "origin/main");
        env.put("GIT_COMMIT", "b".repeat(40));
        env.put("GIT_URL", "https://github.com/example/java-service.git");
        env.put("BUILD_TAG", "jenkins-java-service-42");
        env.put("BUILD_NUMBER", "42");
        env.put("JOB_NAME", "java-service/main");

        List<String> shellCommands = new ArrayList<>();
        List<String> archived = new ArrayList<>();
        List<String> junit = new ArrayList<>();
        Binding binding = new Binding();
        binding.setVariable("env", env);
        binding.setVariable("error", (groovy.lang.Closure<Object>) args -> {
            throw new IllegalStateException(String.valueOf(args[0]));
        });
        binding.setVariable("fileExists", (groovy.lang.Closure<Boolean>) args -> true);
        binding.setVariable("readFile", (groovy.lang.Closure<String>) args -> {
            String path = String.valueOf(args[0]);
            if (path.endsWith("ci/toolchain.json")) return PROJECT_CONFIG;
            if (path.equals(".ci-toolchain-resolution.json")) return ACTIVE_RESOLUTION;
            throw new IllegalArgumentException("Unexpected readFile path: " + path);
        });
        binding.setVariable("dir", (groovy.lang.Closure<Object>) args -> {
            ((groovy.lang.Closure<?>) args[1]).call();
            return null;
        });
        binding.setVariable("deleteDir", (groovy.lang.Closure<Object>) args -> null);
        binding.setVariable("checkout", (groovy.lang.Closure<Object>) args -> null);
        binding.setVariable("sh", (groovy.lang.Closure<Object>) args -> {
            Map<?, ?> options = (Map<?, ?>) args[0];
            String command = String.valueOf(options.get("script"));
            shellCommands.add(command);
            if (Boolean.TRUE.equals(options.get("returnStatus"))) return 0;
            if (Boolean.TRUE.equals(options.get("returnStdout"))) {
                if (command.contains("rev-parse HEAD")) return PLATFORM_SHA + "\n";
                if (command.contains("--format=%ct")) return "1793318400\n";
                return "";
            }
            return null;
        });
        binding.setVariable("withEnv", (groovy.lang.Closure<Object>) args -> {
            List<?> assignments = (List<?>) args[0];
            Map<String, Object> previous = new HashMap<>();
            for (Object assignment : assignments) {
                String[] parts = assignment.toString().split("=", 2);
                previous.put(parts[0], env.get(parts[0]));
                env.put(parts[0], parts.length > 1 ? parts[1] : "");
            }
            try {
                ((groovy.lang.Closure<?>) args[1]).call();
            } finally {
                for (String key : previous.keySet()) {
                    Object value = previous.get(key);
                    if (value == null) env.remove(key);
                    else env.put(key, value);
                }
            }
            return null;
        });
        binding.setVariable("usernamePassword", (groovy.lang.Closure<Object>) args -> args[0]);
        binding.setVariable("file", (groovy.lang.Closure<Object>) args -> args[0]);
        binding.setVariable("withCredentials", (groovy.lang.Closure<Object>) args -> {
            ((groovy.lang.Closure<?>) args[1]).call();
            return null;
        });
        binding.setVariable("junit", (groovy.lang.Closure<Object>) args -> {
            junit.add(String.valueOf(((Map<?, ?>) args[0]).get("testResults")));
            return null;
        });
        binding.setVariable("archiveArtifacts", (groovy.lang.Closure<Object>) args -> {
            archived.add(String.valueOf(((Map<?, ?>) args[0]).get("artifacts")));
            return null;
        });
        script.setBinding(binding);

        Map<String, Object> options = new LinkedHashMap<>();
        options.put("platformRef", PLATFORM_SHA);
        options.put("projectDir", "examples/java17-maven-service");
        options.put("toolchainConfig", "ci/toolchain.json");
        options.put("trustedBranch", "main");
        options.put("registryCredentialsId", "ghcr-readonly");
        options.put("mavenSettingsCredentialsId", "maven-settings-readonly");

        script.invokeMethod("call", new Object[]{options});

        String allCommands = String.join("\n", shellCommands);
        assertTrue(allCommands.contains("resolve_toolchain.py"));
        assertTrue(allCommands.contains("--expected-mode container"));
        assertTrue(allCommands.contains("cosign verify"));
        assertTrue(allCommands.contains("docker pull"));
        assertTrue(allCommands.contains("run_build.py"));
        assertTrue(allCommands.contains("collect_build_metadata.py"));
        assertTrue(allCommands.contains("package_artifact.py"));
        assertTrue(allCommands.contains("verify_artifact.py"));
        assertTrue(allCommands.contains("docker logout"));
        assertTrue(allCommands.contains("rm -f"));
        assertEquals(1, junit.size());
        assertEquals(1, archived.size());
    }

    @Test
    public void sharedLibraryRejectsPullRequestBuildBeforeAnyRegistryOrDockerCall() throws Exception {
        GroovyClassLoader loader = newLoader();
        Path vars = Paths.get("../vars/javaToolchainBuild.groovy").toAbsolutePath().normalize();
        Class<?> scriptClass = loader.parseClass(vars.toFile());
        Script script = (Script) scriptClass.getDeclaredConstructor().newInstance();

        Map<String, Object> env = new HashMap<>();
        env.put("BRANCH_NAME", "PR-47");
        env.put("CHANGE_ID", "47");
        env.put("GIT_COMMIT", "b".repeat(40));
        env.put("GIT_URL", "https://github.com/example/java-service.git");
        List<String> commands = new ArrayList<>();

        Binding binding = new Binding();
        binding.setVariable("env", env);
        binding.setVariable("error", (groovy.lang.Closure<Object>) args -> {
            throw new IllegalStateException(String.valueOf(args[0]));
        });
        binding.setVariable("fileExists", (groovy.lang.Closure<Boolean>) args -> true);
        binding.setVariable("readFile", (groovy.lang.Closure<String>) args -> PROJECT_CONFIG);
        binding.setVariable("sh", (groovy.lang.Closure<Object>) args -> {
            commands.add(String.valueOf(((Map<?, ?>) args[0]).get("script")));
            return 0;
        });
        script.setBinding(binding);

        Map<String, Object> options = Map.of(
                "platformRef", PLATFORM_SHA,
                "projectDir", "examples/java17-maven-service",
                "trustedBranch", "main"
        );
        try {
            script.invokeMethod("call", new Object[]{options});
            fail("Pull Request builds must be rejected");
        } catch (IllegalStateException ex) {
            assertTrue(ex.getMessage().contains("Pull Requests"));
        }
        assertTrue(commands.isEmpty());
    }
}
