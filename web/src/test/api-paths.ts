/**
 * Routes shaped exactly like `openapi-typescript` output (and like the server's envelopes:
 * `Versioned`, `Page`, `JobAccepted`), for testing the generic API helpers before `schema.ts`
 * lists the real routes. Use with `createApiClient<TestPaths>(...)`.
 */

export interface TestThing {
  id: string;
  title: string;
  status: 'draft' | 'approved';
  updatedAt: string;
}

export interface TestVersioned<T> {
  item: T;
  etag: string | null;
}

export interface TestPage<T> {
  items: TestVersioned<T>[];
  nextCursor?: string | null;
}

interface Json<T> {
  headers: Record<string, unknown>;
  content: { 'application/json': T };
}

interface Problem {
  headers: Record<string, unknown>;
  content: { 'application/problem+json': { code?: string; detail?: string } };
}

interface NoOtherMethods {
  options?: never;
  head?: never;
  trace?: never;
}

export interface TestPaths {
  '/api/things': NoOtherMethods & {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
      parameters: {
        query?: { limit?: number; cursor?: string | null; status?: 'draft' | 'approved' | null };
        header?: never;
        path?: never;
        cookie?: never;
      };
      requestBody?: never;
      responses: { 200: Json<TestPage<TestThing>>; 422: Problem };
    };
    post: {
      parameters: { query?: never; header?: never; path?: never; cookie?: never };
      requestBody: { content: { 'application/json': { title: string } } };
      responses: { 201: Json<TestVersioned<TestThing>>; 409: Problem };
    };
    put?: never;
    delete?: never;
    patch?: never;
  };
  '/api/things/{id}': NoOtherMethods & {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
      parameters: { query?: never; header?: never; path: { id: string }; cookie?: never };
      requestBody?: never;
      responses: { 200: Json<TestVersioned<TestThing>>; 404: Problem };
    };
    put: {
      parameters: {
        query?: never;
        header?: { 'If-Match'?: string | null; 'If-None-Match'?: string | null };
        path: { id: string };
        cookie?: never;
      };
      requestBody: { content: { 'application/json': { title: string } } };
      responses: { 200: Json<TestVersioned<TestThing>>; 412: Problem; 428: Problem };
    };
    delete: {
      parameters: { query?: never; header?: never; path: { id: string }; cookie?: never };
      requestBody?: never;
      responses: { 204: { headers: Record<string, unknown>; content?: never } };
    };
    post?: never;
    patch?: never;
  };
  '/api/things/pause': NoOtherMethods & {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    post: {
      parameters: { query?: never; header?: never; path?: never; cookie?: never };
      requestBody?: never;
      responses: { 202: Json<{ jobId: string; status: string }> };
    };
    get?: never;
    put?: never;
    delete?: never;
    patch?: never;
  };
}
