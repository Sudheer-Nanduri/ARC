# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""BaseRule interface for ARC rules."""
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseRule(ABC):
    id: str
    title: str
    description: str

    def __init__(self, descriptor: Dict[str, Any]):
        self.descriptor = descriptor
        self.id = descriptor.get("id", "")
        self.title = descriptor.get("title", "")
        self.description = descriptor.get("description", "")

    @abstractmethod
    def run(self, context, element):
        """Run rule against an element in given context. Return a RuleResult-like dict."""
        raise NotImplementedError()
