package com.iwacollection.cicd.tests;

import groovy.lang.Binding;
import groovy.lang.Closure;
import groovy.lang.GroovyClassLoader;
import groovy.lang.Script;
import org.junit.Test;

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

public class NativeToolchainSharedLibraryTest {
    private static final String PLATFORM_SHA = "eadbbda40f2458935b3413e19bed56e0d61c12aa";
    private static final String DIGEST = "sha256:" + "a".repeat(64);
    private static final String GO_IMAGE = "ghcr.io/iwacollection/cicd-toolchain-go126@" + DIGEST;
    private static final String CPP_IMAGE = "ghcr.io/iwacollection/cicd-toolchain-gcc-host@" + DIGEST;

    private static final String GO_CONFIG = """
            {
              "schema_version": 1,
              "language": "go",
              "project_name": "go-service",
              "toolchain": "go126",
              "build_command": "go build -o bin/app ./...",
              "test_command": "gotestsum --junitfile reports/junit.xml -- ./...",
              "verify_command": "",
              "artifact_globs": ["bin/app"],
              "dependency_lock_files": ["go.sum"],
              "test_report_globs": ["reports/*.xml"]
            }
            """;

    private static final String CPP_CONFIG = """
            {
              "schema_version": 1,
              "project_name": "cpp-service",
              "toolchain": "gcc-host-container-v1",
              "build_command": "cmake -S . -B build -G Ninja && cmake --build build",
              "test_command": "ctest --test-dir build --output-junit ../reports/ctest.xml",
              "artifact_globs": ["build/app"],
              "dependency_lock_files": ["CMakeLists.txt"],
              "test_report_globs": ["reports/*.xml"]
            }
            """;

    private static String resolution(String id, String image) {
        return """
                {
                  "id": "%s",
                  "status": "active",
                  "execution_mode": "container",
                  "image": "%s",
                  "identity": "%s"
                }
                """.formatted(id, image, DIGEST);
    }

    private GroovyClassLoader newLoader() throws Exception {
        Path root = Paths.get("..").toAbsolutePath().normalize();
        GroovyClassLoader loader = new GroovyClassLoader(getClass().getClassLoader());
        loader.addClasspath(root.resolve("src").toString());
        loader.parseClass(root.resolve("src/com/iwacollection/cicd/JavaToolchainSpec.groovy").toFile());
        loader.parseClass(root.resolve("src/com/iwacollection/cicd/NativeToolchainSpec.groovy").toFile());
        return loader;
    }

    private Class<?> spec(GroovyClassLoader loader) throws Exception {
        return loader.loadClass("com.iwacollection.cicd.NativeToolchainSpec");
    }

    private Script loadScript(GroovyClassLoader loader, String name) throws Exception {
        Path vars = Paths.get("../vars/" + name + ".groovy").toAbsolutePath().normalize();
        Class<?> scriptClass = loader.parseClass(vars.toFile());
        return (Script) scriptClass.getDeclaredConstructor().newInstance();
    }

    private static Object invokeExpectingFailure(Method method, String expectedMessagePart, Object... args)
            throws Exception {
        try {
            method.invoke(null, args);
        } catch (InvocationTargetException ex) {
            assertTrue(ex.getCause() instanceof IllegalArgumentException);
            assertTrue("unexpected message: " + ex.getCause().getMessage(),
                    ex.getCause().getMessage().contains(expectedMessagePart));
            return null;
        }
        fail("expected IllegalArgumentException containing: " + expectedMessagePart);
        return null;
    }

    @Test
    public void goAndCppProjectDeclarationsAreAccepted() throws Exception {
        GroovyClassLoader loader = newLoader();
        Method parse = spec(loader).getMethod("parseProjectConfig", String.class, String.class);

        Map<?, ?> go = (Map<?, ?>) parse.invoke(null, GO_CONFIG, "go");
        assertEquals("go126", go.get("toolchain"));
        assertEquals(List.of("go.sum"), go.get("dependency_lock_files"));
        assertEquals(List.of("reports/*.xml"), go.get("test_report_globs"));

        Map<?, ?> cpp = (Map<?, ?>) parse.invoke(null, CPP_CONFIG, "cpp");
        assertEquals("gcc-host-container-v1", cpp.get("toolchain"));
        assertEquals("cpp", cpp.get("language"));
    }

    @Test
    public void projectCannotUseAToolchainOfAnotherLanguage() throws Exception {
        GroovyClassLoader loader = newLoader();
        Method parse = spec(loader).getMethod("parseProjectConfig", String.class, String.class);

        // Go step, gcc toolchain
        invokeExpectingFailure(parse, "is not a go toolchain", CPP_CONFIG, "go");
        // C++ step, Go toolchain
        invokeExpectingFailure(parse, "does not match", GO_CONFIG, "cpp");
        // Unknown language
        invokeExpectingFailure(parse, "language must be", GO_CONFIG, "rust");
    }

    @Test
    public void optionalFieldsMustHaveTheRightType() throws Exception {
        GroovyClassLoader loader = newLoader();
        Method parse = spec(loader).getMethod("parseProjectConfig", String.class, String.class);

        invokeExpectingFailure(parse, "test_command must be a string",
                GO_CONFIG.replace("\"test_command\": \"gotestsum --junitfile reports/junit.xml -- ./...\"",
                        "\"test_command\": 42"), "go");
        invokeExpectingFailure(parse, "test_report_globs must be a string array",
                GO_CONFIG.replace("[\"reports/*.xml\"]", "\"reports/*.xml\""), "go");
        invokeExpectingFailure(parse, "unsafe path segment",
                GO_CONFIG.replace("reports/*.xml", "../reports/*.xml"), "go");
    }

    @Test
    public void onlyActiveContainerToolchainOfTheRightLanguageIsAccepted() throws Exception {
        GroovyClassLoader loader = newLoader();
        Method validate = spec(loader).getMethod("validateResolvedToolchain", Object.class, String.class);
        groovy.json.JsonSlurperClassic json = new groovy.json.JsonSlurperClassic();

        Map<?, ?> ok = (Map<?, ?>) validate.invoke(null, json.parseText(resolution("go126", GO_IMAGE)), "go");
        assertEquals(GO_IMAGE, ok.get("image"));
        assertEquals(DIGEST, ok.get("identity"));

        Map<String, Object> candidate = new LinkedHashMap<>();
        candidate.put("id", "go126");
        candidate.put("status", "candidate");
        candidate.put("execution_mode", "container");
        candidate.put("image", GO_IMAGE);
        candidate.put("identity", DIGEST);
        invokeExpectingFailure(validate, "not Active", candidate, "go");

        invokeExpectingFailure(validate, "is not a cpp toolchain",
                json.parseText(resolution("go126", GO_IMAGE)), "cpp");
        invokeExpectingFailure(validate, "container execution mode",
                json.parseText(resolution("go126", GO_IMAGE).replace("\"container\"", "\"host\"")), "go");
        invokeExpectingFailure(validate, "identity does not match",
                json.parseText(resolution("go126", GO_IMAGE).replace("\"identity\": \"" + DIGEST,
                        "\"identity\": \"sha256:" + "b".repeat(64))), "go");
    }

    @Test
    public void allThreeSharedLibraryScriptsCompile() throws Exception {
        GroovyClassLoader loader = newLoader();
        assertNotNull(loadScript(loader, "nativeToolchainBuild"));
        assertNotNull(loadScript(loader, "goToolchainBuild"));
        assertNotNull(loadScript(loader, "cppToolchainBuild"));
    }

    // ---- full-call harness -------------------------------------------------

    private static final class Harness {
        final Map<String, Object> env = new HashMap<>();
        final List<String> shellCommands = new ArrayList<>();
        final List<String> environmentAssignments = new ArrayList<>();
        final List<String> credentialIds = new ArrayList<>();
        final List<String> archived = new ArrayList<>();
        final List<String> junit = new ArrayList<>();
    }

    private Harness bind(Script script, String repo, String projectConfig, String resolutionJson) {
        Harness h = new Harness();
        h.env.put("BRANCH_NAME", "main");
        h.env.put("GIT_BRANCH", "origin/main");
        h.env.put("GIT_COMMIT", "b".repeat(40));
        h.env.put("GIT_URL", "https://github.com/example/" + repo + ".git");
        h.env.put("BUILD_TAG", "jenkins-" + repo + "-42");
        h.env.put("BUILD_NUMBER", "42");
        h.env.put("JOB_NAME", repo + "/main");

        Binding binding = new Binding();
        binding.setVariable("env", h.env);
        binding.setVariable("error", new Closure<Object>(script) {
            public Object doCall(Object message) {
                throw new IllegalStateException(String.valueOf(message));
            }
        });
        binding.setVariable("fileExists", new Closure<Boolean>(script) {
            public Boolean doCall(Object path) { return true; }
        });
        binding.setVariable("readFile", new Closure<String>(script) {
            public String doCall(Object rawPath) {
                String path = String.valueOf(rawPath);
                if (path.endsWith("ci/toolchain.json")) return projectConfig;
                if (path.equals(".ci-toolchain-resolution.json")) return resolutionJson;
                throw new IllegalArgumentException("Unexpected readFile path: " + path);
            }
        });
        binding.setVariable("dir", new Closure<Object>(script) {
            public Object doCall(Object name, Object body) {
                ((Closure<?>) body).call();
                return null;
            }
        });
        binding.setVariable("deleteDir", new Closure<Object>(script) {
            public Object doCall() { return null; }
        });
        binding.setVariable("checkout", new Closure<Object>(script) {
            public Object doCall(Object configuration) { return null; }
        });
        binding.setVariable("sh", new Closure<Object>(script) {
            public Object doCall(Object rawOptions) {
                Map<?, ?> options = rawOptions instanceof Map
                        ? (Map<?, ?>) rawOptions
                        : java.util.Collections.emptyMap();
                String command = rawOptions instanceof Map
                        ? String.valueOf(options.get("script"))
                        : String.valueOf(rawOptions);
                h.shellCommands.add(command);
                if ("true".equals(h.env.get("CI_MOCK_FAIL_BUILD")) && command.contains("run_build.py")) {
                    throw new IllegalStateException("simulated build failure");
                }
                if (Boolean.TRUE.equals(options.get("returnStatus"))) return 0;
                if (Boolean.TRUE.equals(options.get("returnStdout"))) {
                    if (command.contains("rev-parse HEAD")) return PLATFORM_SHA + "\n";
                    if (command.contains("--format=%ct")) return "1793318400\n";
                    return "";
                }
                return null;
            }
        });
        binding.setVariable("withEnv", new Closure<Object>(script) {
            public Object doCall(Object rawAssignments, Object body) {
                List<?> assignments = (List<?>) rawAssignments;
                Map<String, Object> previous = new HashMap<>();
                for (Object assignment : assignments) {
                    h.environmentAssignments.add(assignment.toString());
                    String[] parts = assignment.toString().split("=", 2);
                    previous.put(parts[0], h.env.get(parts[0]));
                    h.env.put(parts[0], parts.length > 1 ? parts[1] : "");
                }
                try {
                    ((Closure<?>) body).call();
                } finally {
                    for (String key : previous.keySet()) {
                        Object value = previous.get(key);
                        if (value == null) h.env.remove(key);
                        else h.env.put(key, value);
                    }
                }
                return null;
            }
        });
        binding.setVariable("string", new Closure<Object>(script) {
            public Object doCall(Object configuration) { return configuration; }
        });
        binding.setVariable("usernamePassword", new Closure<Object>(script) {
            public Object doCall(Object configuration) { return configuration; }
        });
        binding.setVariable("withCredentials", new Closure<Object>(script) {
            public Object doCall(Object rawCredentials, Object body) {
                List<?> credentials = (List<?>) rawCredentials;
                Map<String, Object> previous = new HashMap<>();
                for (Object rawCredential : credentials) {
                    Map<?, ?> credential = (Map<?, ?>) rawCredential;
                    String id = String.valueOf(credential.get("credentialsId"));
                    h.credentialIds.add(id);
                    for (String field : new String[] {"variable", "usernameVariable", "passwordVariable"}) {
                        if (!credential.containsKey(field)) continue;
                        String variable = String.valueOf(credential.get(field));
                        previous.put(variable, h.env.get(variable));
                        h.env.put(variable, "cicd-platform-sha".equals(id) ? PLATFORM_SHA : "test-secret");
                    }
                }
                try {
                    ((Closure<?>) body).call();
                } finally {
                    for (String key : previous.keySet()) {
                        Object value = previous.get(key);
                        if (value == null) h.env.remove(key);
                        else h.env.put(key, value);
                    }
                }
                return null;
            }
        });
        binding.setVariable("junit", new Closure<Object>(script) {
            public Object doCall(Object rawOptions) {
                h.junit.add(String.valueOf(((Map<?, ?>) rawOptions).get("testResults")));
                return null;
            }
        });
        binding.setVariable("archiveArtifacts", new Closure<Object>(script) {
            public Object doCall(Object rawOptions) {
                h.archived.add(String.valueOf(((Map<?, ?>) rawOptions).get("artifacts")));
                return null;
            }
        });
        script.setBinding(binding);
        return h;
    }

    private static void assertPlatformFlow(Harness h) {
        String allCommands = String.join("\n", h.shellCommands);
        assertTrue(allCommands.contains("resolve_toolchain.py"));
        assertTrue(allCommands.contains("--expected-mode container"));
        assertTrue(allCommands.contains("cosign verify"));
        assertTrue(allCommands.contains("docker pull"));
        assertTrue(allCommands.contains("run_build.py"));
        assertTrue(allCommands.contains("collect_build_metadata.py"));
        assertTrue(allCommands.contains("package_artifact.py"));
        assertTrue(allCommands.contains("verify_artifact.py"));
        assertTrue(allCommands.contains("docker logout"));
        assertTrue(h.credentialIds.contains("cicd-platform-sha"));
    }

    @Test
    public void goBuildRunsPinnedResolverBuildAndArtifactContractAndPublishesJunitEvenOnFailure() throws Exception {
        GroovyClassLoader loader = newLoader();
        Script script = loadScript(loader, "nativeToolchainBuild");
        Harness h = bind(script, "go-service", GO_CONFIG, resolution("go126", GO_IMAGE));

        Map<String, Object> options = new LinkedHashMap<>();
        options.put("language", "go");
        options.put("projectDir", "examples/go-service");
        options.put("registryCredentialsId", "ghcr-readonly");

        script.invokeMethod("call", options);

        assertPlatformFlow(h);
        assertTrue(h.credentialIds.contains("ghcr-readonly"));
        assertTrue(h.environmentAssignments.contains("CI_LANGUAGE=go"));
        assertTrue(h.environmentAssignments.contains("GITHUB_REPOSITORY=example/go-service"));
        assertEquals(List.of("examples/go-service/reports/*.xml"), h.junit);
        assertEquals(1, h.archived.size());
        assertTrue(h.archived.get(0).contains("examples/go-service/reports/*.xml"));

        // A failed build must still publish JUnit results, but not archive a success-only bundle.
        h.env.put("CI_MOCK_FAIL_BUILD", "true");
        try {
            script.invokeMethod("call", options);
            fail("The injected build failure should propagate");
        } catch (IllegalStateException ex) {
            assertTrue(ex.getMessage().contains("simulated build failure"));
        }
        assertEquals(2, h.junit.size());
        assertEquals(1, h.archived.size());
    }

    @Test
    public void cppBuildWorksWithTheActiveGccContainerToolchain() throws Exception {
        GroovyClassLoader loader = newLoader();
        Script script = loadScript(loader, "nativeToolchainBuild");
        Harness h = bind(script, "cpp-service", CPP_CONFIG,
                resolution("gcc-host-container-v1", CPP_IMAGE));

        script.invokeMethod("call", Map.of(
                "language", "cpp",
                "projectDir", "services/cpp",
                "registryCredentialsId", "ghcr-readonly"));

        assertPlatformFlow(h);
        assertTrue(h.environmentAssignments.contains("CI_LANGUAGE=cpp"));
        assertEquals(List.of("services/cpp/reports/*.xml"), h.junit);
    }

    @Test
    public void goStepRefusesAGccToolchainDeclaredByTheProject() throws Exception {
        GroovyClassLoader loader = newLoader();
        Script script = loadScript(loader, "nativeToolchainBuild");
        Harness h = bind(script, "cpp-service", CPP_CONFIG,
                resolution("gcc-host-container-v1", CPP_IMAGE));
        try {
            script.invokeMethod("call", Map.of("language", "go", "projectDir", "services/cpp"));
            fail("A Go step must not consume a gcc toolchain");
        } catch (IllegalStateException ex) {
            assertTrue(ex.getMessage().contains("is not a go toolchain"));
        }
        assertFalse(String.join("\n", h.shellCommands).contains("docker pull"));
    }

    @Test
    public void pullRequestBuildsAreRejectedBeforeAnyShellOrCredentialCall() throws Exception {
        GroovyClassLoader loader = newLoader();
        Script script = loadScript(loader, "nativeToolchainBuild");
        Harness h = bind(script, "go-service", GO_CONFIG, resolution("go126", GO_IMAGE));
        h.env.put("BRANCH_NAME", "PR-47");
        h.env.put("CHANGE_ID", "47");
        try {
            script.invokeMethod("call", Map.of("language", "go"));
            fail("Pull Request builds must be rejected");
        } catch (IllegalStateException ex) {
            assertTrue(ex.getMessage().contains("Pull Requests"));
            assertTrue(ex.getMessage().contains("goToolchainBuild"));
        }
        assertTrue(h.shellCommands.isEmpty());
        assertTrue(h.credentialIds.isEmpty());
    }

    @Test
    public void missingBranchIdentityIsRejectedBeforeAnyShellOrCredentialCall() throws Exception {
        GroovyClassLoader loader = newLoader();
        Script script = loadScript(loader, "nativeToolchainBuild");
        Harness h = bind(script, "go-service", GO_CONFIG, resolution("go126", GO_IMAGE));
        h.env.remove("BRANCH_NAME");
        h.env.remove("GIT_BRANCH");
        try {
            script.invokeMethod("call", Map.of("language", "go"));
            fail("Missing branch identity must be rejected");
        } catch (IllegalStateException ex) {
            assertTrue(ex.getMessage().contains("<unset>"));
            assertTrue(ex.getMessage().contains("trusted branch 'main'"));
        }
        assertTrue(h.shellCommands.isEmpty());
        assertTrue(h.credentialIds.isEmpty());
    }

    @Test
    public void controllerPolicyOptionsCannotBeOverriddenByProjects() throws Exception {
        GroovyClassLoader loader = newLoader();
        Script script = loadScript(loader, "nativeToolchainBuild");
        Harness h = bind(script, "go-service", GO_CONFIG, resolution("go126", GO_IMAGE));
        for (String key : new String[] {"platformRef", "trustedBranch"}) {
            try {
                script.invokeMethod("call", Map.of("language", "go", key, "x"));
                fail(key + " must not be overridable");
            } catch (IllegalStateException ex) {
                assertTrue(ex.getMessage().contains("controller policy"));
            }
        }
        assertTrue(h.shellCommands.isEmpty());
    }

    @Test
    public void wrappersFixTheLanguageAndRejectOverrides() throws Exception {
        GroovyClassLoader loader = newLoader();
        String[][] cases = {{"goToolchainBuild", "go"}, {"cppToolchainBuild", "cpp"}};
        for (String[] c : cases) {
            Script script = loadScript(loader, c[0]);
            List<Object> delegated = new ArrayList<>();
            Binding binding = new Binding();
            binding.setVariable("error", new Closure<Object>(script) {
                public Object doCall(Object message) {
                    throw new IllegalStateException(String.valueOf(message));
                }
            });
            binding.setVariable("nativeToolchainBuild", new Closure<Object>(script) {
                public Object doCall(Object options) {
                    delegated.add(options);
                    return null;
                }
            });
            script.setBinding(binding);

            script.invokeMethod("call", Map.of("projectDir", "app"));
            assertEquals(1, delegated.size());
            Map<?, ?> forwarded = (Map<?, ?>) delegated.get(0);
            assertEquals(c[1], forwarded.get("language"));
            assertEquals("app", forwarded.get("projectDir"));

            try {
                script.invokeMethod("call", Map.of("language", "java"));
                fail(c[0] + " must not accept a language override");
            } catch (IllegalStateException ex) {
                assertTrue(ex.getMessage().contains("language is fixed"));
            }
            assertEquals(1, delegated.size());
        }
    }
}
