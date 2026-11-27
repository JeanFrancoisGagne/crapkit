package com.acme;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class ShapesTest {
    @Test
    public void callsSomeShapes() {
        assertEquals(1, Shapes.ifElse(true));
        assertEquals(0, Shapes.logical(0, 1));
        assertEquals(3, Shapes.branchless(1));
        assertEquals(4, Shapes.twice(2));
        assertEquals(5, new Shapes.Box(5).method(2));
    }
}
