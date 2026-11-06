package com.iwacollection.cicd.tests;

import groovy.lang.GroovyClassLoader;
import org.junit.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.lang.reflect.Method;
import java.util.Map;

import static org.junit.Assert.*;

public class NativeToolchainFixtureTest {
    @Test
    public void checkedInGoAndCppConsumersMatchTheirSharedLibraryContracts() throws Exception {
        Path root = Paths.get("..").toAbsolutePath().normalize();
        GroovyClassLoader loader = new GroovyClassLoader(getClass().getClassLoader());
        loader.addClasspath(root.resolve("src").toString());
        loader.parseClass(root.resolve("src/com/iwacollection/cicd/JavaToolchainSpec.groovy").toFile());
        loader.parseClass(root.resolve("src/com/iwacollection/cicd/NativeToolchainSpec.groovy").toFile());

        Class<?> spec = loader.loadClass("com.iwacollection.cicd.NativeToolchainSpec");
        Method parse = spec.getMethod("parseProjectConfig", String.class, String.class);

        Path goRoot = root.resolve("examples/go-toolchain-consumer");
        Map<?, ?> go = (Map<?, ?>) parse.invoke(null,
                Files.readString(goRoot.resolve("ci/toolchain.json")), "go");
        assertEquals("go126", go.get("toolchain"));
        assertTrue(go.get("build_command").toString().contains("go build"));
        assertTrue(go.get("test_command").toString().contains("gotestsum"));
        assertTrue(Files.isRegularFile(goRoot.resolve("go.mod")));
        assertTrue(Files.isRegularFile(goRoot.resolve("cmd/app/main_test.go")));
        assertTrue(Files.readString(goRoot.resolve("Jenkinsfile")).contains("goToolchainBuild("));

        Path cppRoot = root.resolve("examples/cpp-toolchain-consumer");
        Map<?, ?> cpp = (Map<?, ?>) parse.invoke(null,
                Files.readString(cppRoot.resolve("ci/toolchain.json")), "cpp");
        assertEquals("gcc-host-container-v1", cpp.get("toolchain"));
        assertTrue(cpp.get("build_command").toString().contains("cmake"));
        assertTrue(cpp.get("test_command").toString().contains("ctest"));
        assertTrue(Files.isRegularFile(cppRoot.resolve("CMakeLists.txt")));
        assertTrue(Files.isRegularFile(cppRoot.resolve("tests/message_test.cpp")));
        assertTrue(Files.readString(cppRoot.resolve("Jenkinsfile")).contains("cppToolchainBuild("));
    }
}
