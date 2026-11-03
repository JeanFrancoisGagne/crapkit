# JaCoCo fixtures

Recorded JaCoCo XML reports the `jacoco` reader reads in the unit and e2e
tests, with the CSV report each run wrote beside it. Each was written by the
producer named below, at the version named, with the command shown, from the
root of a fresh copy of `java-repo/` mounted at `/home/user/repo`, and
committed as it came out. JaCoCo is 0.8.12 in both, on OpenJDK 21.0.5 in the
Gradle image and OpenJDK 21.0.7 in the Maven image.

- `gradle-8.10.2.xml` and `gradle-8.10.2.csv`: Gradle 8.10.2 (`gradle:8.10.2-jdk21`),
  with no wrapper, so the command is the gradle stanza `crapkit init` writes
  for a repo with a root `build.gradle` or `build.gradle.kts` and no `gradlew`.
  `build.gradle` turns the XML report on (`xml.required = true`), which
  `jacocoTestReport` leaves off by default, and the CSV report with it.
- Stanza `java-gradle`: `gradle test jacocoTestReport`
- `maven-3.9.9.xml` and `maven-3.9.9.csv`: Maven 3.9.9
  (`maven:3.9.9-eclipse-temurin-21`). The command names the JaCoCo plugin's
  goals by their full coordinates, so `pom.xml` binds no plugin.
- Stanza `java-maven`: `mvn org.jacoco:jacoco-maven-plugin:0.8.12:prepare-agent test org.jacoco:jacoco-maven-plugin:0.8.12:report`

The two reports differ only in the report name (the Gradle project's folder,
the Maven artifact id) and the `sessioninfo` line: their `<sourcefile>` lines
are the same, so the reader reads the same evidence from both.

`java-repo/` is the Java fixture repo. `ShapesTest` runs `ifElse`,
`logical`, `branchless`, `twice` (a lambda) and the inner class `Shapes.Box`;
`grade` (ccn 8) and the default-package `Greeter` never run. JaCoCo writes one
`<sourcefile name="Shapes.java">` for `Shapes` and `Shapes$Box`, and
`Greeter.java` under `<package name="">`.

`WALKTHROUGH.md` takes `java-repo/` from `crapkit init` to a measured scope.

The probe recordings under `tests/accuracy/coverage_oracles/recorded/`,
`jacoco-0.8.12-gradle-8.10.2/` and `jacoco-0.8.12-maven-3.9.9/`, came from
the same two images over `tests/accuracy/coverage_oracles/probes/java/`, in
the `call` and `idle` scenarios. `regenerate.py`'s producers of those names
run the same commands, and its canonical form fixes the `sessioninfo` line.

A producer crapkit reads through `parser = "jacoco"` with no recorded fixture
here: Pester, which writes JaCoCo XML for PowerShell.
